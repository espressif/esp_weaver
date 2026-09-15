# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for judging whether a product is for a device, from the model it reports."""

from custom_components.esp_weaver.profiles.fit import (
    MISFIT_MODEL,
    misfit,
    reported_model,
)
from custom_components.esp_weaver.profiles.product import ProductCandidate
from custom_components.esp_weaver.profiles.schema import (
    MODEL_GENERIC,
    parse_profile_document,
)

LIGHTBULB = "esp.device.lightbulb"
SWITCH = "esp.device.switch"


def _product(model: str, *types: str) -> ProductCandidate:
    """Return a product of the given model declaring the given device types."""
    return ProductCandidate.from_parsed(
        parse_profile_document(
            {
                "version": 1,
                "model": model,
                "vendor": "acme",
                "product": "bulb",
                "product_label": "Bulb",
                "devices": [
                    {"type": device_type, "entities": []} for device_type in types
                ],
            },
            "acme/bulb",
        ),
        source="local",
    )


class TestReportedModel:
    """The model is read out of the node config's ``info``."""

    def test_the_node_config_gives_its_model(self) -> None:
        """``info.model`` as the firmware reported it."""
        assert reported_model({"devices": [], "info": {"model": "A1"}}) == "A1"

    def test_a_node_config_without_a_model_gives_none(self) -> None:
        """No ``info``, an ``info`` without a model, or an empty one: None."""
        assert reported_model({"devices": []}) is None
        assert reported_model({"info": {}}) is None
        assert reported_model({"info": {"model": ""}}) is None
        assert reported_model({"info": "not a mapping"}) is None


class TestMisfit:
    """A product is for the model it names, and nothing else."""

    def test_the_named_model_fits(self) -> None:
        """Byte for byte the product's model: the product's device."""
        assert misfit(_product("A1", LIGHTBULB), "A1") is None

    def test_another_model_is_a_misfit(self) -> None:
        """A vendor's A2 is not its A1."""
        assert misfit(_product("A1", LIGHTBULB), "A2") == MISFIT_MODEL

    def test_no_model_reported_is_a_misfit_for_a_product_naming_one(self) -> None:
        """A product names a model; a device that gives none is not it."""
        assert misfit(_product("A1", LIGHTBULB), None) == MISFIT_MODEL

    def test_a_star_product_is_for_the_star_model_only(self) -> None:
        """``*`` is not a wildcard: a reference profile fits reference firmware."""
        product = _product(MODEL_GENERIC, LIGHTBULB)

        assert misfit(product, MODEL_GENERIC) is None
        assert misfit(product, "A2") == MISFIT_MODEL
        assert misfit(product, None) == MISFIT_MODEL

    def test_device_types_are_not_judged(self) -> None:
        """What the device is made of is settled entity by entity, after the read.

        A product declaring a lightbulb fits a device of its model whatever
        the device reports it is made of; the resolver sorts that out.
        """
        assert misfit(_product("A1", LIGHTBULB), "A1") is None
        assert misfit(_product("A1", LIGHTBULB, SWITCH), "A1") is None
        assert misfit(_product("A1"), "A1") is None
