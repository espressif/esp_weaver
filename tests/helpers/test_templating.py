"""Filling placeholders in text a profile or a translation wrote."""

import pytest

from custom_components.esp_weaver.helpers.templating import render_template


class TestRenderTemplate:
    """Only bare field names are filled; nothing else in the field is reachable."""

    def test_named_fields_are_filled(self) -> None:
        """The ordinary case: names in braces, values in their place."""
        assert (
            render_template(
                "{device}: {sensor} is {value}{unit}",
                {
                    "device": "Kitchen",
                    "sensor": "Temperature",
                    "value": 21.5,
                    "unit": "°C",
                },
            )
            == "Kitchen: Temperature is 21.5°C"
        )

    def test_a_format_spec_and_a_conversion_still_work(self) -> None:
        """What ``str.format`` allows on a bare name is kept."""
        assert render_template("{value:.1f} {name!r}", {"value": 2, "name": "x"}) == (
            "2.0 'x'"
        )

    def test_text_without_placeholders_is_returned_as_is(self) -> None:
        """Braces doubled are literal braces, as in ``str.format``."""
        assert render_template("plain {{text}}", {}) == "plain {text}"

    def test_an_unknown_field_is_a_key_error(self) -> None:
        """Naming a field that was not given is the profile's mistake, and said so."""
        with pytest.raises(KeyError):
            render_template("{missing}", {"present": 1})

    @pytest.mark.parametrize(
        "template",
        [
            "{state.__class__}",
            "{state.__class__.__mro__}",
            "{device[0]}",
            "{}",
            "{0}",
        ],
        ids=["attribute", "attribute chain", "index", "positional", "numbered"],
    )
    def test_anything_but_a_bare_name_is_refused(self, template: str) -> None:
        """A placeholder may name a value, not walk into it or count on order."""
        with pytest.raises(ValueError):
            render_template(template, {"state": 1, "device": "x"})

    def test_a_malformed_template_is_a_value_error(self) -> None:
        """An unclosed brace is not something to guess about."""
        with pytest.raises(ValueError):
            render_template("{device", {"device": "x"})
