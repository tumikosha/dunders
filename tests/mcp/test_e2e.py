import io
import json
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport


def _call(mid, name, arguments):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def test_full_session(tmp_path):
    d = tmp_path / "data"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "note.txt").write_text("find the needle\n")
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": d.as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    srv = McpServer(reg, MountTable(reg, path=f), allow_write=False)

    stream = b"".join(p.encode_message(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        _call(3, "list_mounts", {}),
        _call(4, "grep", {"mount": "root", "path": "", "pattern": "needle"}),
    ])
    writer = io.BytesIO()
    srv.serve(StdioTransport(io.BytesIO(stream), writer))
    out = [json.loads(x) for x in writer.getvalue().splitlines()]
    assert out[0]["result"]["serverInfo"]["name"] == "dunders"
    assert any(t["name"] == "grep" for t in out[1]["result"]["tools"])
    mounts = json.loads(out[2]["result"]["content"][0]["text"])["mounts"]
    assert mounts[0]["label"] == "root"
    grep = json.loads(out[3]["result"]["content"][0]["text"])
    assert grep["matches"][0]["path"] == "sub/note.txt"
