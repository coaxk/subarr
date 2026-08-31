"""#473: detect the exact broken shape our own v1.0.0 docs produced.

The v1.0.0 and v1.1.0 README compose example was:

    volumes:
      - ./subarr/config:/config
      - /path/to/media:/media/library:rw

with no SUBARR_DB_PATH set. The default has always been /data/subarr.db, so
following those instructions mounted a directory subarr does not keep its
database in, and left /data on the container's ephemeral layer. Every recreate
wiped the database and minted a fresh telemetry install_id, which is what
produced ~99% single-ping installs in the fleet data.

These users did not misconfigure anything. They followed our documentation. So
the message has to name the actual mistake and the actual fix, not repeat the
generic "mount something at /data" line they already satisfied in spirit.

⚠️ Note what this deliberately does NOT do: copy the database from /config to
/data. If /data is the ephemeral layer, a copy there is wiped on the next
recreate -- it would look like a migration and fix nothing. The recoverable
data is already in the persistent place; what is wrong is where subarr is
looking.
"""

from __future__ import annotations

from subarr.data_persistence import diagnose_legacy_config_mount


def test_the_exact_v1_doc_shape_is_recognised(tmp_path):
    """/config persistent and holding a db, /data ephemeral."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "subarr.db").write_bytes(b"SQLite format 3\x00")

    d = diagnose_legacy_config_mount(
        db_path=tmp_path / "data" / "subarr.db",
        data_is_ephemeral=True,
        config_dir=cfg,
    )
    assert d is not None
    assert d.legacy_db_path == cfg / "subarr.db"
    assert "/config" in d.message and "/data" in d.message
    assert "SUBARR_DB_PATH" in d.message, "must name the one-line fix"


def test_silent_when_data_is_already_persistent(tmp_path):
    """Nothing to say to a correctly configured install, even if /config
    happens to exist -- plenty of people mount both."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "subarr.db").write_bytes(b"SQLite format 3\x00")

    assert (
        diagnose_legacy_config_mount(
            db_path=tmp_path / "data" / "subarr.db",
            data_is_ephemeral=False,
            config_dir=cfg,
        )
        is None
    )


def test_silent_when_there_is_no_legacy_database(tmp_path):
    """Ephemeral /data with no /config db is the ordinary case the existing
    generic warning already covers. Claiming a legacy mount here would send
    the user hunting for data that does not exist."""
    cfg = tmp_path / "config"
    cfg.mkdir()

    assert (
        diagnose_legacy_config_mount(
            db_path=tmp_path / "data" / "subarr.db",
            data_is_ephemeral=True,
            config_dir=cfg,
        )
        is None
    )


def test_silent_when_config_does_not_exist(tmp_path):
    assert (
        diagnose_legacy_config_mount(
            db_path=tmp_path / "data" / "subarr.db",
            data_is_ephemeral=True,
            config_dir=tmp_path / "nope",
        )
        is None
    )


def test_unknown_persistence_is_not_treated_as_ephemeral(tmp_path):
    """None means we could not tell, which is normal outside a container.
    Guessing here would fire this specific, confident message at bare-metal
    users for whom it is simply wrong."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "subarr.db").write_bytes(b"SQLite format 3\x00")

    assert (
        diagnose_legacy_config_mount(
            db_path=tmp_path / "data" / "subarr.db",
            data_is_ephemeral=None,
            config_dir=cfg,
        )
        is None
    )


def test_message_does_not_tell_them_to_copy_into_the_ephemeral_layer(tmp_path):
    """The tempting-but-wrong advice.

    Copying /config/subarr.db to /data would be erased on the next recreate.
    The fix is to point subarr at the data that is already persistent.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "subarr.db").write_bytes(b"SQLite format 3\x00")

    msg = diagnose_legacy_config_mount(
        db_path=tmp_path / "data" / "subarr.db",
        data_is_ephemeral=True,
        config_dir=cfg,
    ).message.lower()
    assert "copy" not in msg and "move" not in msg
