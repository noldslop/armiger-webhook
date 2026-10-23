FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates\
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 65532 app \
    && useradd --system --uid 65532 --gid app --home-dir /app --shell /usr/sbin/nologin app

ARG ARMIGER_REF=02b77e82ac07cac961ac7182e2310d133f97368d
RUN git clone https://github.com/spencernorton/armiger /app/armiger \
    && cd /app/armiger \
    && git checkout "${ARMIGER_REF}" \
    && rm -rf /app/armiger/.git

# Copy the webhook service
COPY --chown=app:app armiger_webhook.py pyproject.toml uv.lock /app/
RUN uv sync

USER app

ENV ARMIGER_MODEL_DIR=/app/armiger \
    PORT=8096 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache/

EXPOSE 8096

CMD ["uv", "run", "/app/armiger_webhook.py"]
