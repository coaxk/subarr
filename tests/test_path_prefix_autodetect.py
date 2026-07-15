from subarr.integrations.media_server import derive_path_prefix


def test_derives_prefix_from_differing_mount():
    assert (
        derive_path_prefix(["/media/TV", "/media/Movies"], "/media/library", "/media/library/TV/Show/ep.mkv")
        == "/media"
    )


def test_identical_mount_returns_none():
    assert derive_path_prefix(["/media/library"], "/media/library", "/media/library/TV/Show/ep.mkv") is None


def test_file_outside_media_root_returns_none():
    assert derive_path_prefix(["/media/TV"], "/media/library", "/other/TV/ep.mkv") is None


def test_no_matching_location_returns_none():
    assert derive_path_prefix(["/srv/Anime"], "/media/library", "/media/library/TV/ep.mkv") is None


def test_trailing_slashes_tolerated():
    assert derive_path_prefix(["/media/TV/"], "/media/library/", "/media/library/TV/ep.mkv") == "/media"
