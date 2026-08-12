import base64
import json
import pytest
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp.mounts import MountTable
from dunders.mcp import tools, errors


def _local_table(tmp_path, extra=None):
    f = tmp_path / "bm.json"
    items = [{"label": "root", "uri": (tmp_path / "data").as_uri()}]
    if extra:
        items += extra
    f.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    return MountTable(reg, path=f), reg


def _seed(tmp_path):
    d = tmp_path / "data"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("hello\nworld\n")
    (d / "sub" / "b.txt").write_text("needle here\n")
    (d / "bin.dat").write_bytes(b"\x00\x01\x02BIN")
    return d


def test_list_mounts(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("list_mounts", {}, table=table, registry=reg, allow_write=False)
    labels = {m["label"] for m in out["mounts"]}
    assert labels == {"root"}
    assert out["mounts"][0]["scheme"] == "file"


def test_list_dir(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("list_dir", {"mount": "root", "path": ""},
                          table=table, registry=reg, allow_write=False)
    names = {e["name"]: e for e in out["entries"]}
    assert set(names) == {"sub", "a.txt", "bin.dat"}
    assert names["sub"]["is_dir"] and not names["a.txt"]["is_dir"]
    assert names["a.txt"]["size"] == 12


def test_read_file_text(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file", {"mount": "root", "path": "a.txt"},
                          table=table, registry=reg, allow_write=False)
    assert out["encoding"] == "text" and out["data"] == "hello\nworld\n"


def test_read_file_binary_base64(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file", {"mount": "root", "path": "bin.dat"},
                          table=table, registry=reg, allow_write=False)
    assert out["encoding"] == "base64"
    assert base64.b64decode(out["data"]) == b"\x00\x01\x02BIN"


def test_read_file_offset_and_cap(tmp_path):
    d = _seed(tmp_path)
    (d / "big.txt").write_text("X" * (tools.READ_CAP + 500))
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file",
                          {"mount": "root", "path": "big.txt", "offset": 10},
                          table=table, registry=reg, allow_write=False)
    assert len(out["data"]) == tools.READ_CAP  # capped
    assert out["eof"] is False


def test_read_file_negative_length_clamped(tmp_path):
    d = _seed(tmp_path)
    (d / "big.txt").write_text("X" * (tools.READ_CAP + 500))
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file",
                          {"mount": "root", "path": "big.txt", "length": -1},
                          table=table, registry=reg, allow_write=False)
    assert len(out["data"]) <= tools.READ_CAP  # negative length must not bypass the cap


def test_stat(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("stat", {"mount": "root", "path": "sub"},
                          table=table, registry=reg, allow_write=False)
    assert out["name"] == "sub" and out["is_dir"] is True


def test_search_glob(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("search", {"mount": "root", "path": "", "glob": "*.txt"},
                          table=table, registry=reg, allow_write=False)
    assert set(out["matches"]) == {"a.txt", "sub/b.txt"}


def test_grep_content(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("grep",
                          {"mount": "root", "path": "", "pattern": "needle"},
                          table=table, registry=reg, allow_write=False)
    assert len(out["matches"]) == 1
    hit = out["matches"][0]
    assert hit["path"] == "sub/b.txt" and hit["line"] == 1 and "needle" in hit["text"]


def test_grep_skips_binary(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("grep",
                          {"mount": "root", "path": "", "pattern": "BIN"},
                          table=table, registry=reg, allow_write=False)
    assert out["matches"] == []  # bin.dat skipped (NUL sniff)


def test_specs_read_only_has_no_write_tools(tmp_path):
    names = {t["name"] for t in tools.tool_specs(allow_write=False)}
    assert {"list_mounts", "list_dir", "read_file", "stat", "search", "grep"} <= names
    assert "write_file" not in names and "delete" not in names


def test_unknown_tool_raises(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    with pytest.raises(errors.McpError):
        tools.call_tool("nope", {}, table=table, registry=reg, allow_write=False)


def test_write_tools_present_only_with_allow_write():
    names = {t["name"] for t in tools.tool_specs(allow_write=True)}
    assert {"write_file", "mkdir", "delete", "copy"} <= names


def test_write_file_creates(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("write_file",
                          {"mount": "root", "path": "new.txt", "content": "hi"},
                          table=table, registry=reg, allow_write=True)
    assert out["written"] == 2
    assert (tmp_path / "data" / "new.txt").read_text() == "hi"


def test_write_tool_absent_when_disabled(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    with pytest.raises(errors.McpError):
        tools.call_tool("write_file", {"mount": "root", "path": "x", "content": "y"},
                        table=table, registry=reg, allow_write=False)


def test_mkdir_and_delete(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    tools.call_tool("mkdir", {"mount": "root", "path": "fresh"},
                    table=table, registry=reg, allow_write=True)
    assert (tmp_path / "data" / "fresh").is_dir()
    tools.call_tool("delete", {"mount": "root", "path": "a.txt"},
                    table=table, registry=reg, allow_write=True)
    assert not (tmp_path / "data" / "a.txt").exists()


def test_copy_within_mount(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    tools.call_tool("copy",
                    {"src_mount": "root", "src_path": "a.txt",
                     "dst_mount": "root", "dst_path": "sub"},
                    table=table, registry=reg, allow_write=True)
    assert (tmp_path / "data" / "sub" / "a.txt").read_text() == "hello\nworld\n"
