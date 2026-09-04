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
# packages (first hit: CVE-2026-45447 in libssl3t64; then CVE-2026-40393 in
# libgbm1, a transitive ffmpeg dep). Install FIRST, THEN upgrade, so the upgrade
# also patches ffmpeg/mkvtoolnix's transitive deps to +debNuN — upgrading before
# the install left those freshly-pulled deps at the unpatched base version.
# Patch-don't-suppress, same as subarr-subgen.
#
# APT_REFRESH exists because the GHA layer cache would otherwise serve this
# layer for days: neither `update` nor `upgrade` re-runs on a cache hit, so a
# security patch that Debian has already published silently never lands and we
# ship a known-fixed CVE (caught by the trivy gate on libtiff CVE-2026-12912,
# fixed in +deb13u3 while the cached layer still had u2). CI passes the current
# UTC date, so this layer expires once a day and every scan + published image
# gets current packages. Referenced in the RUN so the value actually busts it.
#
# The trailing `pip install --upgrade pip` is BUILD-TIME hygiene, not a
# shipped-image fix (pip is deleted again after the app install below). The
# `apt-get upgrade` above structurally CANNOT reach it: python:3.12-slim
# installs pip into /usr/local/lib/python3.12/site-packages via get-pip, so no
# Debian package owns it (`dpkg -S` on that path finds no match) and apt has
# nothing to upgrade. Debian's own python3-pip is 25.1.1 anyway, below the
# 26.2.0 floor. Several of the pip CVEs are wheel-install path traversals
# (CVE-2026-8643 lets a malicious entry point name overwrite arbitrary files),
# and installing wheels from PyPI is exactly what the next layer does, so the
# build runs under a patched resolver. Unpinned on purpose, same reasoning as
# `apt-get upgrade -y`: a pin goes stale and re-opens the class. It rides the
# APT_REFRESH layer so it inherits the daily cache-bust.
ARG APT_REFRESH=0
RUN echo "apt-refresh=${APT_REFRESH}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg mkvtoolnix passwd util-linux \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

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
# #364 slice 2: also bake the local spoken-LID runtime ([lid] is the same
# onnxruntime/numpy pair as [vad], listed separately so it can diverge later).
# The small silero-lang95 model is NOT baked; pulled + checksum-verified lazily
# on first forced-segment scan, same opt-in pattern as [vad]'s silero VAD model.
# Nothing in a running subarr container installs packages: the entrypoint is
# shell, the healthcheck and CMD are `python`. So pip is build-time-only, and
# shipping it is pure attack surface plus a permanent CVE feed. Deleting it
# beats upgrading it, because upgrading CANNOT win: pip carries its
# dependencies vendored, `pip/_vendor/vendor.txt` pins setuptools==70.3.0 in
# every version to date, and CVE-2025-47273 against it is not fixed until
# setuptools 78.1.1. pip 26.2.1 also ships a licenses/ tree that makes those
# vendored pins visible to trivy for the first time, so merely upgrading
# TRADED six pip CVEs for two fixable HIGHs (setuptools + msgpack) and turned
# the trivy gate red. Measured, not assumed: trivy on the published image, an
# upgrade-only build and this one gave 6 / 3 / 0 pip-family findings, with
# fixable HIGH/CRITICAL at 0 / 2 / 0.
# ensurepip is cleared too, or `python -m ensurepip` restores pip 25.0.1 from
# the wheel bundled in the stdlib — the ORIGINAL vulnerable version, which
# trivy never flagged because it does not scan inside .whl files.
RUN pip install --no-cache-dir ".[vad,qe-onnx,lid]" \
    && pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip/_bundled

# #237: non-root runtime. Create a default subarr user/group (1000:1000,
# overridable at runtime via PUID/PGID by the entrypoint). HF_HOME moves the QE
# model cache off /root onto the writable data volume — also fixes it persisting
# across restarts (previously it re-downloaded to ephemeral /root/.cache).
# No `USER` directive: the entrypoint must start as root to chown /data + the
# socket-gid grant, then it DROPS to the non-root user before exec.
ENV HF_HOME=/data/.cache/huggingface
RUN groupadd -g 1000 subarr \
    && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin subarr
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# Justified suppression below: no static USER because the entrypoint must START
# as root to chown a pre-existing root-owned /data and grant docker-socket
# access, then it DROPS to the non-root PUID/PGID via setpriv before exec. The
# running process is non-root (verified: PID 1 boots as uid=1000 under
# cap_drop:ALL). The static rule can't observe the runtime privilege drop.
# See docs/superpowers/specs/2026-06-18-nonroot-container-design.md.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 9922

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9922/api/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "subarr.app"]
