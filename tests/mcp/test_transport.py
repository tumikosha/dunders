import io
from dunders.mcp import protocol as p
from dunders.mcp.transport import StdioTransport


def _reader(*msgs):
    buf = b"".join(p.encode_message(m) for m in msgs)
    return io.BytesIO(buf)


def test_read_messages_until_eof():
    r = _reader({"id": 1, "method": "a"}, {"id": 2, "method": "b"})
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 1, "method": "a"}
    assert t.read_message() == {"id": 2, "method": "b"}
    assert t.read_message() is None  # EOF


def test_write_message_frames_with_newline():
    w = io.BytesIO()
    StdioTransport(io.BytesIO(), w).write_message({"id": 3, "result": {}})
    assert w.getvalue() == p.encode_message({"id": 3, "result": {}})


def test_blank_line_skipped():
    r = io.BytesIO(b"\n" + p.encode_message({"id": 1, "method": "a"}))
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 1, "method": "a"}


def test_bad_line_skipped_not_fatal():
    r = io.BytesIO(b"{bad}\n" + p.encode_message({"id": 9, "method": "ok"}))
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 9, "method": "ok"}
