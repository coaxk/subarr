FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# ffmpeg package ships both ffmpeg + ffprobe. Some distros split the package;
# Debian slim keeps them together. Pin to `ffmpeg` (not `ffprobe`) for that
# reason. Adds ~150MB to the image but it's the canonical install path and
# the probe subsystem (v1.1 batch 1 hotfix) needs it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install build deps only if/when we add native wheels. Keep image lean for now.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

EXPOSE 9922

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9922/api/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "subarr.app"]
