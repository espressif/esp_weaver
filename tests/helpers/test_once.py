"""Saying a thing in the log once."""

import logging

import pytest

from custom_components.esp_weaver.helpers import once
from custom_components.esp_weaver.helpers.once import forget_said, log_once

_LOGGER = logging.getLogger("tests.once")


class TestLogOnce:
    """The same message is one line; a different one is another."""

    def test_a_repeated_message_is_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A device that reconnects every minute does not fill the log."""
        for _ in range(3):
            log_once(_LOGGER, logging.WARNING, "Parameter %r disagrees", "Power")

        assert caplog.text.count("Parameter 'Power' disagrees") == 1

    def test_different_arguments_are_different_messages(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The same warning about another parameter is worth its own line."""
        log_once(_LOGGER, logging.WARNING, "Parameter %r disagrees", "Power")
        log_once(_LOGGER, logging.WARNING, "Parameter %r disagrees", "Level")

        assert "Parameter 'Power' disagrees" in caplog.text
        assert "Parameter 'Level' disagrees" in caplog.text

    def test_unhashable_arguments_are_fine(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Lists and dicts, as the resolver passes, are compared by their text."""
        log_once(_LOGGER, logging.WARNING, "bounds %s vs %s", {"min": 0}, [1, 2])
        log_once(_LOGGER, logging.WARNING, "bounds %s vs %s", {"min": 0}, [1, 2])

        assert caplog.text.count("bounds") == 1

    def test_forgetting_lets_it_be_said_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """What tests rely on between themselves."""
        log_once(_LOGGER, logging.WARNING, "said again")
        forget_said()
        log_once(_LOGGER, logging.WARNING, "said again")

        assert caplog.text.count("said again") == 2

    def test_the_memory_is_bounded(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A message with an ever-changing value cannot grow the set for ever."""
        monkeypatch.setattr(once, "_REMEMBERED_LIMIT", 3)
        for i in range(3):
            log_once(_LOGGER, logging.WARNING, "value %s", i)
        # The fourth distinct message empties the memory before it is kept, so
        # the first message may be said again.
        log_once(_LOGGER, logging.WARNING, "value %s", 99)
        log_once(_LOGGER, logging.WARNING, "value %s", 0)

        assert caplog.text.count("value 0") == 2
