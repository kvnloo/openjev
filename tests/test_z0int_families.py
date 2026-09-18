from z0int.families import family_of


def test_family_map():
    assert family_of("read_file") == "READ_SEARCH"
    assert family_of("search_files") == "READ_SEARCH"
    assert family_of("patch") == "EDIT"
    assert family_of("write_file") == "EDIT"
    assert family_of("terminal") == "EXECUTE"
    assert family_of("web_search") == "WEB"
    assert family_of("delegate_task") == "DELEGATE"
    assert family_of(None) == "RESPOND"
