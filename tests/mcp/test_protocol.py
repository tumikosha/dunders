import pytest
from dunders.mcp import protocol as p


def test_encode_appends_newline_and_roundtrips():
    raw = p.encode_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert raw.endswith(b"\n")
    assert p.decode_message(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_encode_is_utf8_not_ascii_escaped():
    raw = p.encode_message({"x": "papká"})
    assert "papká" in raw.decode("utf-8")


def test_decode_bad_json_raises_protocol_error():
    with pytest.raises(p.ProtocolError):
        p.decode_message(b"{not json}\n")


def test_decode_non_object_raises_protocol_error():
    with pytest.raises(p.ProtocolError):
        p.decode_message(b"[1, 2, 3]\n")


def test_make_response_and_error_shape():
    assert p.make_response(7, {"a": 1}) == {"jsonrpc": "2.0", "id": 7, "result": {"a": 1}}
    err = p.make_error(7, p.INVALID_PARAMS, "bad")
    assert err == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": "bad"}}
    err2 = p.make_error(7, p.INTERNAL_ERROR, "boom", data={"detail": "x"})
    assert err2["error"]["data"] == {"detail": "x"}


def test_protocol_version_constant():
    assert p.PROTOCOL_VERSION == "2025-06-18"
