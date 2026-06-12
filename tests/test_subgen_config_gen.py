"""Mode A artifacts (spec §2.5): exact text the user pastes."""

from __future__ import annotations


def test_env_additions_block(subarr_env):
    from subarr.subgen_config_gen import generate_env_additions

    text = generate_env_additions(model="large-v3", device="cuda", compute_type="float16")
    assert "WHISPER_MODEL: large-v3" in text
    assert "TRANSCRIBE_DEVICE: cuda" in text
    assert "COMPUTE_TYPE: float16" in text
    # compose `environment:` mapping style — indented keys, no leading dashes
    assert not text.strip().startswith("-")


def test_full_compose_gpu(subarr_env):
    from subarr.subgen_config_gen import generate_full_compose

    text = generate_full_compose(
        model="large-v3",
        device="cuda",
        compute_type="float16",
        media_host_path="/mnt/media",
        port=9000,
        gpu=True,
    )
    assert "image: ghcr.io/coaxk/subarr-subgen:latest" in text
    assert "WHISPER_MODEL: large-v3" in text
    assert "driver: nvidia" in text
    assert "/mnt/media:/media" in text
    assert "9000:9000" in text
    # kwargs/regroup must NOT appear — they come baked from r9
    assert "SUBGEN_KWARGS" not in text
    assert "CUSTOM_REGROUP" not in text


def test_full_compose_cpu_has_no_gpu_block(subarr_env):
    from subarr.subgen_config_gen import generate_full_compose

    text = generate_full_compose(
        model="small",
        device="cpu",
        compute_type="int8",
        media_host_path="/mnt/media",
        port=9000,
        gpu=False,
    )
    assert "driver: nvidia" not in text
    assert "TRANSCRIBE_DEVICE: cpu" in text


def test_full_compose_is_valid_yaml(subarr_env):
    import yaml

    from subarr.subgen_config_gen import generate_full_compose

    for gpu in (True, False):
        text = generate_full_compose(
            model="medium",
            device="cuda" if gpu else "cpu",
            compute_type="float16" if gpu else "int8",
            media_host_path="/mnt/media",
            port=9000,
            gpu=gpu,
        )
        doc = yaml.safe_load(text)
        assert doc["services"]["subgen"]["image"].startswith("ghcr.io/coaxk/subarr-subgen")
        env = doc["services"]["subgen"]["environment"]
        assert env["WHISPER_MODEL"] == "medium"


def test_detection_passthrough_snippet(subarr_env):
    from subarr.subgen_config_gen import detection_passthrough_snippet

    text = detection_passthrough_snippet()
    assert "driver: nvidia" in text
    assert "utility" in text  # utility-only: queries the card,
    assert "compute" not in text  # reserves no compute/VRAM from subgen
