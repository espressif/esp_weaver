# Contributing to ESP-Weaver

Thank you for your interest in contributing to ESP-Weaver! This document provides guidelines for contributing to this project.

## Getting Started

### Prerequisites

- Python 3.13.2 or higher
- Home Assistant Core 2025.12.5 or higher
- An ESP device running ESP-Weaver firmware with local control (see the README, section "Device Firmware")

### Development Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/espressif/esp_weaver.git
   cd esp_weaver
   ```

2. Create a virtual environment and install the test and lint dependencies. `requirements_test.txt` is the single list; it pulls in Home Assistant through `pytest-homeassistant-custom-component`:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements_test.txt
   ```

   Cloud control is not supported in this release (`CLOUD_CONTROL_ENABLED` is
   False); the tests mock the `esp-rainmaker-cli` subprocess, so nothing beyond
   `requirements_test.txt` is needed.

3. Run the tests to verify the setup:

   ```bash
   pytest tests/
   ```

4. To try the integration in a Home Assistant instance, copy it into that instance's `custom_components` directory. `install.sh` does this on Linux and macOS:

   ```bash
   ./install.sh /path/to/homeassistant/config
   ```

   On any platform:

   ```bash
   python -c "import shutil; shutil.copytree('custom_components/esp_weaver', '/path/to/config/custom_components/esp_weaver', dirs_exist_ok=True)"
   ```

## Code Style

The checks below are what CI runs; the versions are pinned in `requirements_test.txt`. Run them all before pushing:

```bash
ruff format custom_components tests tools
ruff check custom_components tests tools
mypy custom_components/esp_weaver tests
pylint custom_components/esp_weaver
codespell
yamllint .gitlab-ci.yml .github custom_components/esp_weaver/quality_scale.yaml custom_components/esp_weaver/device_profiles
python tools/check_profile_keys.py
python tools/check_shipped_profiles.py --repository ../esp-weaver-profiles   # with a checkout of the profile repository; --sync copies it over
python tools/check_profile_code_sync.py --repository ../esp-weaver-profiles  # same checkout; --sync copies the profile code over
```

`iot/local_ctrl/` is excluded from ruff, mypy and pylint in `pyproject.toml`;
pylint runs over the integration only.

The test suite has to stay above 95% line coverage, and the config flow (`config_flow.py` and the `flows/` package) at 100%:

```bash
pytest tests/ --cov=custom_components/esp_weaver --cov-fail-under=95
pytest tests/ -k config_flow --cov=custom_components.esp_weaver.config_flow --cov=custom_components.esp_weaver.flows --cov-fail-under=100
```

### Style Guidelines

- Follow the Home Assistant core conventions; `pyproject.toml` carries the same ruff, mypy and pylint configuration
- Type hints on every function; mypy runs in strict mode over the whole package
- Docstrings on every public function and class, one sentence saying what it does
- Lines under 88 characters
- `async`/`await` for all I/O; blocking calls go through `hass.async_add_executor_job`. Ruff's `ASYNC` rules fail the build on a blocking call inside a coroutine
- `iot/` must not import `homeassistant`: it talks to its host through the `IotHost` protocol in `helpers/host.py`
- `iot/local_ctrl/` is derived from the esp-idf `esp_local_ctrl` tooling and kept in its style; it is excluded from the lint and coverage gates

## Change Process

The repository is developed on GitLab and mirrored to GitHub. The default branch is `master`.

1. Create a branch from `master`, named `<type>/<short-description>`, where `<type>` is one of the Conventional Commits types below
2. Make your changes, with tests for anything new or fixed
3. Run the checks above
4. Commit with a [Conventional Commits](https://www.conventionalcommits.org/) message: `feat: ...`, `fix: ...`, `docs: ...`, `refactor: ...`, `test: ...`, `chore: ...`
5. Add an entry to `CHANGELOG.md` under the version being prepared, in its `Added`, `Changed` or `Fixed` list, written for the user: what changes for them, not which function moved
6. Open a merge request against `master`; CI has to pass before review

A pull request opened on GitHub cannot be merged there: the mirror is one
way. A maintainer fetches it into a branch on GitLab and merges it as a merge
request; the next mirror run carries it to GitHub.

Product profiles are not in this repository. They live in the profile
repository, [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles), whose README says how to
submit one and how its `index.json` is kept current. The version 1 profile
logic exists twice: `custom_components/esp_weaver/profiles/` here, and
`tools/profilelib/` in that repository, whose own `tools/build_index.sh`
runs it with PyYAML and voluptuous alone. Keep the modules here (`index`,
and what it imports: `schema`, `product`) free of Home Assistant - a test
checks it - and keep the two copies identical: edit both, or copy this
repository's over with the tool below. No CI compares the copies (or the
shipped profiles) across the two repositories: they merge independently,
and maintainers run the comparisons below by hand against a local checkout
of the profile repository before merging a change that touches both.

Espressif's products also live in that repository (`vendors/espressif/`). The
integration ships a copy under
`custom_components/esp_weaver/device_profiles/espressif/`; maintainers update
that copy when the repository changes (commit and merge as usual). From a
checkout of the profile repository::

    python tools/check_shipped_profiles.py --repository ../esp-weaver-profiles   # compare; --sync copies it over
    python tools/check_profile_code_sync.py --repository ../esp-weaver-profiles  # compare the code copies; --sync copies them over

CI also runs `tools/check_profile_keys.py` so no device type declares the
same entity key twice.


### Guidelines

- Keep each change to one feature or fix
- Update the README and `docs/device_profiles_in_detail.md` when behaviour they describe changes; `README_zh.md` and `docs/device_profiles_in_detail_zh.md` are kept in step with them
- A new string goes in `strings.json` and in both `translations/*.json`; a new step or error in the flow needs a test
- A new or changed entity key in a shipped profile changes the `unique_id` of existing entities; `tools/check_profile_keys.py` lists them

## Reporting Issues

Please use GitHub Issues to report bugs or request features.

### Bug Reports

Include the following information:

- Home Assistant version
- ESP-Weaver integration version
- ESP device model and firmware version
- The product profile the device is bound to
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (Settings → System → Logs), and the integration's diagnostics download if the device is set up

### Feature Requests

Describe:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

## Code of Conduct

Please be respectful and constructive in all interactions. We're all here to learn and improve.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
