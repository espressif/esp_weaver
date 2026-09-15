# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Test the payload parsing of the local control codec."""

from typing import Any
from unittest.mock import MagicMock

from cryptography.exceptions import InvalidTag
import pytest

from custom_components.esp_weaver.iot.local_ctrl import local_ctrl_codec
from custom_components.esp_weaver.iot.local_ctrl.protocol import local_ctrl_pb2

# The generated module builds its message classes at import time, so they are
# not visible to a type checker.
LocalCtrlMessage: Any = local_ctrl_pb2.LocalCtrlMessage  # type: ignore[attr-defined]


def _security_ctx_returning(message: Any) -> MagicMock:
    """Return a security context that decrypts to one already-built message."""
    security_ctx = MagicMock()
    security_ctx.decrypt_data.return_value = message.SerializeToString()
    return security_ctx


class TestParsePayloadSetResponse:
    """Test how the answer to a write is read."""

    @pytest.mark.parametrize("status", [1, 2, 255])
    def test_a_refused_write_is_reported_as_refused(self, status: int) -> None:
        """The status of a write is read from resp_set_prop_vals."""
        message = LocalCtrlMessage()
        message.resp_set_prop_vals.status = status

        parsed = local_ctrl_codec.parse_payload(
            "query_response", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == status

    def test_an_accepted_write_is_reported_as_accepted(self) -> None:
        """Test that a write the device took is still read as success."""
        message = LocalCtrlMessage()
        message.resp_set_prop_vals.status = 0

        parsed = local_ctrl_codec.parse_payload(
            "query_response", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == 0
        assert parsed["properties"] == []


class TestParsePayloadOtherResponses:
    """Test that reading a write did not disturb the other message types."""

    def test_a_property_count(self) -> None:
        """Test that a count is read out of its own field."""
        message = LocalCtrlMessage()
        message.resp_get_prop_count.count = 7

        parsed = local_ctrl_codec.parse_payload(
            "query_response", _security_ctx_returning(message), b""
        )

        assert parsed == {"status": 0, "count": 7, "properties": []}

    def test_property_values(self) -> None:
        """Test that a read still yields its properties."""
        message = LocalCtrlMessage()
        message.resp_get_prop_vals.status = 0
        prop = message.resp_get_prop_vals.props.add()
        prop.name = "params"
        prop.value = b'{"Light": {"Power": true}}'

        parsed = local_ctrl_codec.parse_payload(
            "query_response", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == 0
        assert [p["name"] for p in parsed["properties"]] == ["params"]

    def test_a_failed_read_yields_no_properties(self) -> None:
        """Test that a read the device refused does not look like an empty device."""
        message = LocalCtrlMessage()
        message.resp_get_prop_vals.status = 3

        parsed = local_ctrl_codec.parse_payload(
            "query_response", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == 3
        assert parsed["properties"] == []

    def test_an_active_report(self) -> None:
        """Test that a report the device sent unasked is read by index."""
        message = LocalCtrlMessage()
        prop = message.report_prop_vals.props.add()
        prop.index = 1
        prop.value = b'{"Light": {"Power": false}}'

        parsed = local_ctrl_codec.parse_payload(
            "active_report", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == 0
        assert parsed["properties"] == [
            {"index": 1, "value": b'{"Light": {"Power": false}}'}
        ]

    def test_an_unknown_message_type(self) -> None:
        """Test that a message type nobody sends is refused rather than guessed."""
        message = LocalCtrlMessage()
        message.resp_set_prop_vals.status = 0

        parsed = local_ctrl_codec.parse_payload(
            "something_else", _security_ctx_returning(message), b""
        )

        assert parsed["status"] == -1
        assert "error" in parsed

    def test_an_undecryptable_payload(self) -> None:
        """Test that a payload that cannot be read is not read as a success."""
        security_ctx = MagicMock()
        security_ctx.decrypt_data.side_effect = ValueError("bad key")

        parsed = local_ctrl_codec.parse_payload(
            "query_response", security_ctx, b"nonsense"
        )

        assert parsed["status"] == -1

    def test_a_failed_authentication_tag_is_a_bad_frame(self) -> None:
        """AES-GCM rejecting a frame must end as status -1, not as an exception."""
        security_ctx = MagicMock()
        security_ctx.decrypt_data.side_effect = InvalidTag()

        parsed = local_ctrl_codec.parse_payload(
            "active_report", security_ctx, b"nonsense"
        )

        assert parsed["status"] == -1
        assert parsed["properties"] == []

    def test_an_undecodable_protobuf_is_a_bad_frame(self) -> None:
        """Decrypted bytes that are not a LocalCtrlMessage end as status -1."""
        security_ctx = MagicMock()
        security_ctx.decrypt_data.return_value = b"\xff\xff\xff\xff\xff"

        parsed = local_ctrl_codec.parse_payload(
            "query_response", security_ctx, b"whatever"
        )

        assert parsed["status"] == -1
        assert "error" in parsed
