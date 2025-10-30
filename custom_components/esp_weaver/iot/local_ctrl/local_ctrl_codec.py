# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
"""Codec module for ESP Local Control protobuf message encoding/decoding.

This module provides functions to create and parse protobuf messages for
property count, property values, and set property operations.
"""

import logging
from typing import TYPE_CHECKING

from .protocol import local_ctrl_pb2

if TYPE_CHECKING:
    from .security.security import Security

_LOGGER = logging.getLogger(__name__)


def to_bytes(s: str | bytes) -> bytes:
    """Convert string or bytes to bytes using latin-1 encoding."""
    if isinstance(s, bytes):
        return s
    return bytes(s, encoding="latin-1")


def get_prop_count_request(security_ctx: "Security") -> str:
    """Create encrypted request for property count."""
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdGetPropertyCount
    payload = local_ctrl_pb2.CmdGetPropertyCount()
    req.cmd_get_prop_count.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def get_prop_count_response(
    security_ctx: "Security", response_data: str | bytes
) -> int:
    """Parse encrypted response for property count."""
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    if resp.resp_get_prop_count.status == 0:
        return resp.resp_get_prop_count.count
    return 0


def get_prop_vals_request(
    security_ctx: "Security", indices: list[int]
) -> str:
    """Create encrypted request for property values."""
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdGetPropertyValues
    payload = local_ctrl_pb2.CmdGetPropertyValues()
    payload.indices.extend(indices)
    req.cmd_get_prop_vals.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def get_prop_vals_response(
    security_ctx: "Security", response_data: str | bytes
) -> list[dict[str, str | int | bytes]]:
    """Parse encrypted response for property values."""
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    results: list[dict[str, str | int | bytes]] = []
    if resp.resp_get_prop_vals.status == 0:
        for prop in resp.resp_get_prop_vals.props:
            results.append(
                {
                    "name": prop.name,
                    "type": prop.type,
                    "flags": prop.flags,
                    "value": prop.value,
                }
            )
    return results


def set_prop_vals_request(
    security_ctx: "Security", indices: list[int], values: list[bytes]
) -> str:
    """Create encrypted request to set property values."""
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdSetPropertyValues
    payload = local_ctrl_pb2.CmdSetPropertyValues()
    for i, v in zip(indices, values):
        prop = payload.props.add()
        prop.index = i
        prop.value = v
    req.cmd_set_prop_vals.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def set_prop_vals_response(
    security_ctx: "Security", response_data: str | bytes
) -> bool:
    """Parse encrypted response for set property values."""
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    return resp.resp_set_prop_vals.status == 0


def parse_payload(msg_type_str, security_ctx, payload_data):
    """Parse protobuf payload from HTTP message.

    Args:
        msg_type_str: Message type string (active_report or query_response).
        security_ctx: Security context for decryption.
        payload_data: Encrypted payload data.

    Returns:
        Dictionary with status and parsed properties.
    """
    try:
        decrypt_data = security_ctx.decrypt_data(to_bytes(payload_data))
        resp = local_ctrl_pb2.LocalCtrlMessage()
        resp.ParseFromString(decrypt_data)

        if resp.HasField("resp_get_prop_count"):
            if resp.resp_get_prop_vals and len(resp.resp_get_prop_vals.props) == 0:
                return {
                    "status": 0,
                    "count": resp.resp_get_prop_count.count,
                    "properties": [],
                }

        results = []
        status = 0

        if msg_type_str == "active_report":
            if resp.HasField("report_prop_vals"):
                # ReportPropertyValues has no status field - always successful if parsed
                for prop in resp.report_prop_vals.props:
                    results.append({"index": prop.index, "value": prop.value})
                status = 0

        elif msg_type_str == "query_response":
            if resp.HasField("resp_get_prop_vals"):
                status = resp.resp_get_prop_vals.status
                if status == 0:
                    for prop in resp.resp_get_prop_vals.props:
                        results.append(
                            {
                                "name": prop.name,
                                "type": prop.type,
                                "flags": prop.flags,
                                "value": prop.value,
                            }
                        )
        else:
            return {
                "status": -1,
                "properties": [],
                "error": f"Unknown message type: {msg_type_str}",
            }

        return {"status": status, "properties": results}

    except (ValueError, TypeError, AttributeError) as e:
        _LOGGER.error(
            "Failed to parse protobuf payload: %s (type=%s)", e, msg_type_str
        )
        return {"status": -1, "properties": [], "error": str(e)}
