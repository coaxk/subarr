"""Tests for Tier-3 API-key auto-extract.

Covers the 4-source resolver chain for each *arr service:
  1. subarr's own env vars
  2. container's docker-inspect env
  3. mounted config file (canonical)
  4. .env file alongside the mount

Per-parser tests use realistic fixture configs taken from actual
Bazarr/Sonarr/Radarr/Tautulli installs.

Conflict handling: multiple sources with different values → all in
candidates, conflict=True, authoritative chosen by priority order.
"""

from __future__ import annotations

from pathlib import Path


from subarr.config_extractor import (
    ExtractedKey,
    SERVICE_CONFIGS,
    extract_all,
    extract_for_service,
)


# ─── Fixtures ───────────────────────────────────────────────────────


BAZARR_CONFIG_YAML = """\
auth:
  type: form
  apikey: '1234567890abcdef1234567890abcdef'
  username: admin
  password: 'hashed-blob-here'

general:
  ip: 0.0.0.0
  port: 6767
"""

SONARR_CONFIG_XML = """\
<Config>
  <BindAddress>*</BindAddress>
  <Port>8989</Port>
  <UrlBase></UrlBase>
  <InstanceName>Sonarr</InstanceName>
  <ApiKey>abcdef1234567890abcdef1234567890</ApiKey>
  <AuthenticationMethod>Forms</AuthenticationMethod>
  <Branch>main</Branch>
</Config>
"""

RADARR_CONFIG_XML = """\
<Config>
  <Port>7878</Port>
  <ApiKey>fedcba0987654321fedcba0987654321</ApiKey>
  <Branch>master</Branch>
</Config>
"""

TAUTULLI_CONFIG_INI = """\
[General]
api_key = aabbccddeeff00112233445566778899
http_port = 8181

[Plex]
pms_token = unrelated-token-here
"""


def _make_config_dir(tmp_path: Path, service: str, content: str) -> Path:
    spec = SERVICE_CONFIGS[service]
    d = tmp_path / service
    d.mkdir()
    (d / spec.config_filename).write_text(content, encoding="utf-8")
    return d


# ─── Per-parser tests ──────────────────────────────────────────────


def test_extract_bazarr_from_config_yaml(tmp_path: Path):
    d = _make_config_dir(tmp_path, "bazarr", BAZARR_CONFIG_YAML)
    r = extract_for_service("bazarr", subarr_env={}, config_dir=d)
    assert r.key is not None
    assert r.key.api_key == "1234567890abcdef1234567890abcdef"
    assert r.key.source == "config file"
    assert r.key.source_detail.endswith("config.yaml")


def test_extract_sonarr_from_config_xml(tmp_path: Path):
    d = _make_config_dir(tmp_path, "sonarr", SONARR_CONFIG_XML)
    r = extract_for_service("sonarr", subarr_env={}, config_dir=d)
    assert r.key.api_key == "abcdef1234567890abcdef1234567890"
    assert r.key.source == "config file"


def test_extract_radarr_from_config_xml(tmp_path: Path):
    d = _make_config_dir(tmp_path, "radarr", RADARR_CONFIG_XML)
    r = extract_for_service("radarr", subarr_env={}, config_dir=d)
    assert r.key.api_key == "fedcba0987654321fedcba0987654321"


def test_extract_tautulli_from_config_ini(tmp_path: Path):
    d = _make_config_dir(tmp_path, "tautulli", TAUTULLI_CONFIG_INI)
    r = extract_for_service("tautulli", subarr_env={}, config_dir=d)
    assert r.key.api_key == "aabbccddeeff00112233445566778899"


# ─── Resolver chain tests ──────────────────────────────────────────


def test_subarr_env_wins_when_no_config(tmp_path: Path):
    """Source 1: subarr's own env. No config dir → only candidate."""
    r = extract_for_service(
        "bazarr",
        subarr_env={"BAZARR_API_KEY": "from-subarr-env-1234"},
        config_dir=None,
    )
    assert r.key.api_key == "from-subarr-env-1234"
    assert r.key.source == "subarr env"
    assert r.conflict is False


def test_container_env_picked_up(tmp_path: Path):
    """Source 2: docker inspect Config.Env array (KEY=VAL strings)."""
    r = extract_for_service(
        "bazarr",
        subarr_env={},
        container_env=[
            "TZ=UTC",
            "PUID=1000",
            "BAZARR_API_KEY=from-docker-env-9999",
            "PATH=/usr/local/bin:/usr/bin",
        ],
        config_dir=None,
    )
    assert r.key.api_key == "from-docker-env-9999"
    assert r.key.source == "docker env"


def test_dot_env_file_picked_up(tmp_path: Path):
    """Source 4: .env file alongside the config dir."""
    d = tmp_path / "bazarr"
    d.mkdir()
    (d / ".env").write_text(
        "# bazarr stack env\nTZ=UTC\nBAZARR_API_KEY=from-dotenv-5555\n",
        encoding="utf-8",
    )
    r = extract_for_service("bazarr", subarr_env={}, config_dir=d)
    assert r.key.api_key == "from-dotenv-5555"
    assert r.key.source == ".env file"


def test_config_file_beats_subarr_env_when_both_present(tmp_path: Path):
    """All four sources have the same priority order. config file
    is the authoritative one when conflict — that's what the *arr
    app actually reads at boot."""
    d = _make_config_dir(tmp_path, "sonarr", SONARR_CONFIG_XML)
    r = extract_for_service(
        "sonarr",
        subarr_env={"SONARR_API_KEY": "stale-from-subarr-env"},
        container_env=["SONARR_API_KEY=stale-from-docker-env"],
        config_dir=d,
    )
    # All four candidates collected
    assert len(r.candidates) >= 2
    # Conflict detected (different values)
    assert r.conflict is True
    # The authoritative chosen is the config file
    assert r.key.source == "config file"
    assert r.key.api_key == "abcdef1234567890abcdef1234567890"


def test_no_conflict_when_all_sources_agree(tmp_path: Path):
    """If subarr env + container env + config all have the same value,
    no conflict surfaces (all candidates, but same key)."""
    d = _make_config_dir(tmp_path, "sonarr", SONARR_CONFIG_XML)
    same_key = "abcdef1234567890abcdef1234567890"
    r = extract_for_service(
        "sonarr",
        subarr_env={"SONARR_API_KEY": same_key},
        container_env=[f"SONARR_API_KEY={same_key}"],
        config_dir=d,
    )
    assert r.conflict is False
    assert r.key.api_key == same_key


def test_no_candidates_returns_none(tmp_path: Path):
    r = extract_for_service("bazarr", subarr_env={}, config_dir=None)
    assert r.key is None
    assert r.candidates == []
    assert r.conflict is False


def test_unknown_service_returns_empty():
    r = extract_for_service("nonexistent", subarr_env={"X": "y"})
    assert r.key is None
    assert r.candidates == []


# ─── Edge cases ────────────────────────────────────────────────────


def test_malformed_yaml_doesnt_crash(tmp_path: Path):
    d = tmp_path / "bazarr"
    d.mkdir()
    (d / "config.yaml").write_text("this is :: garbage : : not yaml\n", encoding="utf-8")
    # Should just return no candidates, not raise
    r = extract_for_service("bazarr", subarr_env={}, config_dir=d)
    assert r.key is None


def test_xml_without_apikey_element_returns_none(tmp_path: Path):
    d = tmp_path / "sonarr"
    d.mkdir()
    (d / "config.xml").write_text("<Config><Port>8989</Port></Config>", encoding="utf-8")
    r = extract_for_service("sonarr", subarr_env={}, config_dir=d)
    assert r.key is None


def test_empty_apikey_in_config_ignored(tmp_path: Path):
    """Empty/whitespace API key in config file shouldn't match."""
    d = tmp_path / "sonarr"
    d.mkdir()
    (d / "config.xml").write_text(
        "<Config><ApiKey></ApiKey></Config>",
        encoding="utf-8",
    )
    r = extract_for_service("sonarr", subarr_env={}, config_dir=d)
    assert r.key is None


def test_dot_env_handles_quoted_values(tmp_path: Path):
    d = tmp_path / "bazarr"
    d.mkdir()
    (d / ".env").write_text(
        'BAZARR_API_KEY="quoted-key-here"\n',
        encoding="utf-8",
    )
    r = extract_for_service("bazarr", subarr_env={}, config_dir=d)
    assert r.key.api_key == "quoted-key-here"


def test_dot_env_ignores_comments(tmp_path: Path):
    d = tmp_path / "bazarr"
    d.mkdir()
    (d / ".env").write_text(
        "# BAZARR_API_KEY=commented-out\nBAZARR_API_KEY=real-one\n",
        encoding="utf-8",
    )
    r = extract_for_service("bazarr", subarr_env={}, config_dir=d)
    assert r.key.api_key == "real-one"


# ─── Aggregator + serialisation tests ──────────────────────────────


def test_extract_all_runs_every_service(tmp_path: Path):
    """extract_all() runs the resolver chain for every known service
    + returns a dict keyed by service name."""
    bazarr_dir = _make_config_dir(tmp_path, "bazarr", BAZARR_CONFIG_YAML)
    sonarr_dir = _make_config_dir(tmp_path, "sonarr", SONARR_CONFIG_XML)
    results = extract_all(
        config_dirs={"bazarr": bazarr_dir, "sonarr": sonarr_dir},
        subarr_env={},
    )
    assert results["bazarr"].key.api_key.startswith("12345")
    assert results["sonarr"].key.api_key.startswith("abcdef")
    # Services without a config dir → no key found, no crash
    assert results["radarr"].key is None
    assert results["tautulli"].key is None


def test_extracted_key_masking():
    k = ExtractedKey(
        service="bazarr",
        api_key="1234567890abcdef1234567890abcdef",
        source="config file",
    )
    assert k.masked == "••••cdef"
    assert k.api_key not in k.masked  # never leak full key in masked


def test_extracted_key_to_dict_omits_raw_key():
    """to_dict() returns masked_key not the raw key — critical for
    API responses that surface this info."""
    k = ExtractedKey(
        service="bazarr",
        api_key="secretkey1234",
        source="config file",
        source_detail="/host-configs/bazarr/config.yaml",
    )
    d = k.to_dict()
    assert "api_key" not in d
    assert d["masked_key"] == "••••1234"
    assert d["source"] == "config file"


def test_extract_result_to_dict_serialisation(tmp_path: Path):
    d = _make_config_dir(tmp_path, "sonarr", SONARR_CONFIG_XML)
    r = extract_for_service("sonarr", subarr_env={}, config_dir=d)
    out = r.to_dict()
    assert out["found"] is True
    assert out["chosen"]["service"] == "sonarr"
    assert "api_key" not in str(out["chosen"])  # masked, not raw
    assert out["candidate_count"] == 1
    assert out["conflict"] is False
