import io
import json
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
import dunders.mcp as mcp
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.transport import StdioTransport


def test_run_stdio_serves_over_given_transport(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": (tmp_path / "data").as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    table = MountTable(reg, path=f)

    reader = io.BytesIO(p.encode_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    writer = io.BytesIO()
    monkeypatch.setattr(StdioTransport, "stdio",
                        classmethod(lambda cls: StdioTransport(reader, writer)))
    mcp.run_stdio(reg, table, allow_write=False)
    assert json.loads(writer.getvalue())["result"]["serverInfo"]["name"] == "dunders"
