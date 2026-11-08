FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates\
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 65532 app \
    && useradd --system --uid 65532 --gid app --home-dir /app --shell /usr/sbin/nologin app

ARG ARMIGER_REF=ad5a1e3dec56842d5b0aa6ef881151a4b87dd2a8
RUN git clone https://github.com/spencernorton/armiger /app/armiger \
    && cd /app/armiger \
    && git checkout "${ARMIGER_REF}" \
    && rm -rf /app/armiger/.git

# Copy the webhook service
COPY --chown=app:app armiger_webhook.py pyproject.toml uv.lock /app/
RUN uv sync

# Model weights no longer ship in the armiger checkout. Bake the "ultra"
# preset into the image at build time; each ADD is content-verified against
# its SHA-256 (build fails on any mismatch or a moved HF revision).
ADD --chmod=0444 --checksum=sha256:e3133bf37b9bafb34bbf8ff240c8562c2a1063aabadf6df6460c80d8046aae06 https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/config.json /app/models/config.json
ADD --chmod=0444 --checksum=sha256:c10afea430188e0207752e93bf0b340fbb585858684037c5134a477cbc0dd7bb https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/vocab.json /app/models/vocab.json
ADD --chmod=0444 --checksum=sha256:27d04c6e0832b7f8bce3d9564497d85f6cca7bf4c90895c07bc8417c243f64ef https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/calibration.json /app/models/calibration.json
ADD --chmod=0444 --checksum=sha256:e12880d60aea7abc50f39d39576f0d36d4069e66fc54599e0f388eaa6fe657f0 https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/armiger_ultra.fp32.onnx /app/models/armiger_ultra.fp32.onnx
ADD --chmod=0444 --checksum=sha256:4e4c7a2dd59fb91a234e6627e7ade35062f99268d3f05370432bb9f2a2f9b1c9 https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/swarm_adapter.pt /app/models/swarm_adapter.pt
ADD --chmod=0444 --checksum=sha256:8a8b7a4ba97cb22c3aa1f75ab33762052deb09580df464b1eabfe622a2837d26 https://huggingface.co/NorviTech/armiger-ultra/resolve/41cb0db4c3dfce2e3866fb67d414f0ca7026af2a/validated_zero_cue_catalog.json /app/models/validated_zero_cue_catalog.json
# Remote ADD applies --chmod to the auto-created parent directory too. Keep
# the dir listable while the baked model files stay read-only 0444. The
# offline check re-verifies every file against the armiger manifest and
# fails the build if anything is missing or invalid.
RUN chmod 0755 /app/models \
    && PYTHONPATH=/app/armiger/src python -m armiger download --directory /app/models --variant ultra --offline

USER app

ENV ARMIGER_CODE_DIR=/app/armiger \
    ARMIGER_MODEL_DIR=/app/models \
    PORT=8096 \
    PYTHONPATH=/app/armiger/src \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache/

EXPOSE 8096

CMD ["uv", "run", "/app/armiger_webhook.py"]
