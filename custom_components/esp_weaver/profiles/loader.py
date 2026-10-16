# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Reads product profiles off disk: the ones shipped, and the vendor's own.

The integration's ``device_profiles/`` holds the service layer and Espressif's
products; vendors' go anywhere under ``<config>/esp_weaver/``. Each file is one
product, offered as one choice, and names its own vendor and product; where it
sits says nothing.
"""

import logging
from pathlib import Path
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from ..identity import DOMAIN
from .product import SOURCE_BUILTIN, ProductCandidate, ProfileProblem
from .registry import ProfileRegistry
from .schema import (
    YAML_SUFFIXES,
    DeviceProfile,
    ProfileError,
    Validator,
    parse_profile,
    parse_profile_text,
    profile_blocks,
    validate_profile,
    validate_service_layer,
)

_LOGGER = logging.getLogger(__name__)

PROFILE_DIR: Final[Path] = Path(__file__).parent.parent / "device_profiles"

# The service layer: every RainMaker node's services, mapped once so that no
# product has to restate them.
SERVICE_LAYER_FILE: Final[str] = "service_layer.yaml"

# The vendor whose products ship with the integration.
BUILTIN_VENDOR: Final[str] = "espressif"

# The directory under the configuration folder that holds the vendor's own.
CUSTOM_PROFILE_DIR: Final[str] = DOMAIN

PROBLEM_INVALID: Final[str] = "invalid"
PROBLEM_MISPLACED: Final[str] = "misplaced"

# Prefix of every repair about a profile file, so a load can clear its own.
ISSUE_PREFIX: Final[str] = "profile_file_"


def read_profile_file(
    path: Path, shown: str | None = None, validator: Validator = validate_profile
) -> dict[str, Any]:
    """Parse and validate a single profile file. Blocking.

    Raises:
        ProfileError: The file cannot be read, parsed or validated.
    """
    name = shown or path.name
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ProfileError(f"Cannot read profile {name}: {err}") from err
    except UnicodeDecodeError as err:
        # The shipped profiles carry comments in Chinese, so a copy re-saved by
        # an editor that defaults to a local codepage is a likely mistake.
        raise ProfileError(
            f"Profile {name} is not UTF-8 encoded: {err}. Re-save the file as UTF-8."
        ) from err

    return parse_profile_text(raw, name, validator)


def _yaml_files(directory: Path) -> list[Path]:
    """Return the YAML files under a directory, recursively, in path order. Blocking."""
    try:
        if not directory.is_dir():
            return []
        return sorted(
            p
            for p in directory.rglob("*")
            if p.is_file()
            and p.suffix in YAML_SUFFIXES
            # A leading dot is an editor's backup or the importer's temporary
            # file, never a profile someone meant to offer.
            and not p.name.startswith(".")
        )
    except OSError as err:
        _LOGGER.error("Cannot list profiles in %s: %s", directory, err)
        return []


def _load_product(
    path: Path,
    root: Path,
    source: str,
    problems: list[ProfileProblem],
) -> ProductCandidate | None:
    """Read one file as a product, recording a problem instead of raising.

    One unusable file costs only the product it is; an exception here would
    fail every device's setup.
    """
    shown = str(path.relative_to(root))
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        detail = f"Cannot read profile {shown}: {err}"
        _LOGGER.error("%s", detail)
        problems.append(ProfileProblem(kind=PROBLEM_INVALID, path=path, detail=detail))
        return None
    except UnicodeDecodeError as err:
        detail = (
            f"Profile {shown} is not UTF-8 encoded: {err}. Re-save the file as UTF-8."
        )
        _LOGGER.error("%s", detail)
        problems.append(ProfileProblem(kind=PROBLEM_INVALID, path=path, detail=detail))
        return None

    try:
        parsed = parse_profile(raw, shown)
    except ProfileError as err:
        _LOGGER.error("%s", err)
        problems.append(
            ProfileProblem(kind=PROBLEM_INVALID, path=path, detail=str(err))
        )
        return None
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected failure reading profile %s", shown)
        problems.append(
            ProfileProblem(
                kind=PROBLEM_INVALID,
                path=path,
                detail=f"Could not read profile {shown}; see the log for details.",
            )
        )
        return None

    return ProductCandidate.from_parsed(
        parsed, path=path, location=shown, source=source
    )


def load_service_layer() -> list[DeviceProfile]:
    """Read the built-in service layer. Blocking.

    Raises:
        ProfileError: The shipped file is unreadable (a packaging fault).
    """
    validated = read_profile_file(
        PROFILE_DIR / SERVICE_LAYER_FILE, validator=validate_service_layer
    )
    _, services = profile_blocks(validated, SERVICE_LAYER_FILE)
    return services


def scan_builtin(source: str) -> tuple[list[ProductCandidate], list[ProfileProblem]]:
    """Read the products shipped with the integration. Blocking.

    A file a vendor left under ``device_profiles/`` works, and is flagged:
    an upgrade deletes it.
    """
    products: list[ProductCandidate] = []
    problems: list[ProfileProblem] = []

    for path in _yaml_files(PROFILE_DIR):
        if path == PROFILE_DIR / SERVICE_LAYER_FILE:
            continue
        loaded = _load_product(path, PROFILE_DIR, source, problems)
        if loaded is None:
            continue
        products.append(loaded)
        if loaded.vendor != BUILTIN_VENDOR:
            _LOGGER.warning(
                "Profile %s sits in %s and is being offered from there, but "
                "updating ESP-Weaver replaces that directory and will delete it. "
                "Move it to the %s directory in your Home Assistant configuration "
                "folder to keep it",
                path.relative_to(PROFILE_DIR),
                PROFILE_DIR,
                CUSTOM_PROFILE_DIR,
            )
            problems.append(ProfileProblem(kind=PROBLEM_MISPLACED, path=path))

    return products, problems


def builtin_registry(product: str) -> ProfileRegistry:
    """Return the registry for a shipped product. Blocking; for tooling and tests.

    Raises:
        ProfileError: The product is not among the shipped ones.
    """
    products, _ = scan_builtin(SOURCE_BUILTIN)
    for candidate in products:
        if candidate.id == product and candidate.content is not None:
            devices, services = profile_blocks(candidate.content, candidate.id)
            return ProfileRegistry.for_product(
                candidate.id, devices, services, load_service_layer()
            )
    raise ProfileError(f"No shipped product {product!r} in {PROFILE_DIR}")


def _prepare_custom_dir(custom_dir: Path) -> None:
    """Create the vendor profile directory if it is not there yet. Blocking."""
    try:
        custom_dir.mkdir(exist_ok=True)
    except OSError as err:
        # A read-only config directory is unusual but not our business to fix.
        _LOGGER.debug("Could not create %s: %s", custom_dir, err)


def scan_local(
    custom_dir: Path, source: str
) -> tuple[list[ProductCandidate], list[ProfileProblem]]:
    """Read the vendor's own products from the configuration directory. Blocking."""
    _prepare_custom_dir(custom_dir)
    products: list[ProductCandidate] = []
    problems: list[ProfileProblem] = []
    for path in _yaml_files(custom_dir):
        loaded = _load_product(path, custom_dir, source, problems)
        if loaded is not None:
            products.append(loaded)
    return products, problems


@callback
def sync_file_issues(hass: HomeAssistant, problems: list[ProfileProblem]) -> None:
    """Mirror the load problems into repairs, dropping ones now fixed."""
    wanted = {
        f"{ISSUE_PREFIX}{problem.kind}_{problem.issue_name}": problem
        for problem in problems
    }

    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(ISSUE_PREFIX):
            if issue_id not in wanted:
                ir.async_delete_issue(hass, DOMAIN, issue_id)

    for issue_id, problem in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=f"profile_{problem.kind}",
            translation_placeholders={
                "filename": problem.filename,
                "directory": str(problem.path.parent),
                "detail": problem.detail,
                "custom_dir": CUSTOM_PROFILE_DIR,
            },
        )
