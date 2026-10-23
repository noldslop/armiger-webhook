# Base image is a build arg so in-cluster builds can point at the local
# registry mirror (trow) to avoid docker.io rate limits.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# System packages: git (clone armiger at build time), curl (healthcheck)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned) for fast, reproducible Python dependency installation.
RUN pip install --no-cache-dir uv==0.7.15

# Python dependencies.
# torch is installed CPU-only from the PyTorch index (keeps the image small);
# the rest come from PyPI.
RUN uv pip install --system torch --default-index https://download.pytorch.org/whl/cpu \
    && uv pip install --system numpy onnxruntime cryptography \
    && uv cache clean

# Unprivileged runtime user (Debian's "nobody" UID/GID).
RUN groupadd --system --gid 65532 app \
    && useradd --system --uid 65532 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Clone the armiger model + modules, pinned to a specific commit for
# reproducibility. Override with --build-arg ARMIGER_REF=<sha-or-branch>.
ARG ARMIGER_REF=02b77e82ac07cac961ac7182e2310d133f97368d
RUN git clone https://github.com/spencernorton/armiger /app/armiger \
    && cd /app/armiger \
    && git checkout "${ARMIGER_REF}" \
    && rm -rf /app/armiger/.git

# Copy the webhook service
COPY --chown=app:app armiger_webhook.py /app/

USER app

ENV ARMIGER_MODEL_DIR=/app/armiger \
    PORT=8096 \
    PYTHONUNBUFFERED=1

EXPOSE 8096

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8096/healthz || exit 1

CMD ["python", "/app/armiger_webhook.py"]
