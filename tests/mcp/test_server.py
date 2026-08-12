import io
import json
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport


def _table(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("hi")
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": (tmp_path / "data").as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    return reg, MountTable(reg, path=f)


def _req(mid, method, params=None):
    m = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        m["params"] = params
    return m


def test_initialize_handshake(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(1, "initialize", {}))
    assert resp["result"]["protocolVersion"] == p.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "dunders"
    assert "tools" in resp["result"]["capabilities"]


def test_notifications_initialized_returns_none(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    assert srv.handle(_req(None, "notifications/initialized")) is None


def test_tools_list_read_only(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    names = {t["name"] for t in srv.handle(_req(2, "tools/list"))["result"]["tools"]}
    assert "read_file" in names and "write_file" not in names


def test_tools_list_write_enabled(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=True)
    names = {t["name"] for t in srv.handle(_req(2, "tools/list"))["result"]["tools"]}
    assert "write_file" in names


def test_tools_call_read_file(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(3, "tools/call",
                           {"name": "read_file",
                            "arguments": {"mount": "root", "path": "a.txt"}}))
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["data"] == "hi"


def test_unknown_method_is_error_not_crash(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(4, "no/such/method"))
    assert resp["error"]["code"] == p.METHOD_NOT_FOUND


def test_tools_call_unknown_mount_maps_error(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(5, "tools/call",
                           {"name": "list_dir", "arguments": {"mount": "ghost"}}))
    assert resp["error"]["code"] == -32001  # MOUNT_NOT_FOUND


def test_serve_loop_over_transport(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    reader = io.BytesIO(
        p.encode_message(_req(1, "initialize", {}))
        + p.encode_message(_req(2, "tools/list"))
    )
    writer = io.BytesIO()
    srv.serve(StdioTransport(reader, writer))
    lines = writer.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == 1 and json.loads(lines[1])["id"] == 2
