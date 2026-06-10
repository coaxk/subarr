FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# ffmpeg package ships both ffmpeg + ffprobe. Some distros split the package;
# Debian slim keeps them together. Pin to `ffmpeg` (not `ffprobe`) for that
# reason. Adds ~150MB to the image but it's the canonical install path and
# the probe subsystem (v1.1 batch 1 hotfix) needs it.
# mkvtoolnix provides `mkvpropedit` for the #159 default-audio-track swap — an
# in-place Matroska header edit (no remux) that makes a show's original-language
# track the default so subgen stops transcribing a dub into double-translated subs.
# `apt-get upgrade` pulls base-image security patches between python:3.12-slim
# rebuilds — the trivy gate fails on fixable HIGH/CRITICAL CVEs in base
# packages (first hit: CVE-2026-45447 in libssl3t64, fixed in deb13u2 while
# the base still shipped u1). Patch-don't-suppress, same as subarr-subgen.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ffmpeg mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install build deps only if/when we add native wheels. Keep image lean for now.
COPY pyproject.toml ./
COPY src/ ./src/

# #111: bake the speech-aware audio (silero VAD) runtime — onnxruntime + numpy
# (~65MB, NO torch). The ~2MB silero model is NOT baked; it's pulled on opt-in
# from onboarding to /config so the image stays model-free and the user makes
# the explicit choice. Without the model present, subarr falls back to the
# ffmpeg silencedetect picker, so this extra is inert until enabled.
# #179: also bake the no-torch QE runtime ([qe-onnx] adds tokenizers +
# safetensors + huggingface_hub on top of [vad]'s onnxruntime/numpy — ~MBs,
# NO torch). The ~1.9GB LaBSE ONNX model is NOT baked; it's pulled into the
# HF cache on first QE use. Until then the judge stays structural-only.
RUN pip install --no-cache-dir ".[vad,qe-onnx]"

EXPOSE 9922

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9922/api/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "subarr.app"]
