from dunders.mcp import errors as e


def test_mcperror_carries_code_message_data():
    err = e.McpError(e.MOUNT_NOT_FOUND, "no mount 'x'", data={"label": "x"})
    assert err.code == -32001 and err.message == "no mount 'x'"
    assert err.data == {"label": "x"}


def test_map_file_not_found():
    m = e.map_exception(FileNotFoundError("gone"))
    assert m.code == e.PATH_NOT_FOUND


def test_map_permission_error():
    assert e.map_exception(PermissionError()).code == e.ACCESS_DENIED


def test_map_passthrough_mcperror():
    orig = e.McpError(e.ACCESS_DENIED, "denied")
    assert e.map_exception(orig) is orig


def test_map_generic_hides_detail():
    m = e.map_exception(RuntimeError("secret sftp://user:pw@host failed"))
    assert m.code == e.PROVIDER_ERROR
    assert "pw" not in m.message and "sftp" not in m.message
