FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates\
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 65532 app \
    && useradd --system --uid 65532 --gid app --home-dir /app --shell /usr/sbin/nologin app

ARG ARMIGER_REF=7fb5ef2b521cd9158a389f5cb7bdb286813ba4d1
RUN git clone https://github.com/spencernorton/armiger /app/armiger \
    && cd /app/armiger \
    && git checkout "${ARMIGER_REF}" \
    && rm -rf /app/armiger/.git

# Copy the webhook service
COPY --chown=app:app armiger_webhook.py pyproject.toml uv.lock /app/
RUN uv sync

# Model weights no longer ship in the armiger checkout; they are downloaded
# from HuggingFace (checksum verified) at first start. Give the non-root
# runtime user a writable dir for them.
RUN mkdir -p /app/models && chown app:app /app/models

USER app

ENV ARMIGER_CODE_DIR=/app/armiger \
    ARMIGER_MODEL_DIR=/app/models \
    PORT=8096 \
    PYTHONPATH=/app/armiger/src \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache/

EXPOSE 8096

CMD ["uv", "run", "/app/armiger_webhook.py"]
