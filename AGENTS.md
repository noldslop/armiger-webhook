# AGENTS.md

This file provides guidance for AI coding agents working with code in this repository.

## What this is

A single-file Python HTTP service (`armiger_webhook.py`, stdlib `http.server` only — no web framework) that wraps the [Armiger Ultra](https://github.com/spencernorton/armiger) torrent classifier as an HTTP service speaking the **bitmagnet classifier webhook** contract. It exposes `POST /classify` and `GET /healthz` on `PORT` (default 8096).

There is no test suite and no linter configured in this repo.

## Commands

Dependency management is uv (see `pyproject.toml` + `uv.lock`):

```bash
uv sync                          # install deps into .venv
ARMIGER_CODE_DIR=<armiger-clone> ARMIGER_MODEL_DIR=<models-dir> uv run armiger_webhook.py
```

- `ARMIGER_CODE_DIR` (default `/app/armiger`) points at a git checkout of `spencernorton/armiger`; the service prepends both `<dir>` and `<dir>/src` to `sys.path` and imports `ArmigerUltraPipeline` from the `armiger` package. (The package lives under `src/` as of armiger `7fb5ef2`.)
- `ARMIGER_MODEL_DIR` (default `/app/models`) is a **data-only** directory, no longer the git checkout. Model weights were removed from the armiger repo and are now fetched from HuggingFace (`NorviTech/armiger-ultra`, SHA-256 verified via `armiger.artifacts.download_model`) at first load. The dir must be writable by the running user.
- `torch` is pulled CPU-only from the pytorch wheel index (`[tool.uv.sources]`), mirroring the Dockerfile.

The Armiger repo itself is not vendored — it is cloned at build time in the Dockerfile at the pinned `ARMIGER_REF`. To run locally, clone armiger at that commit, point `ARMIGER_CODE_DIR` at it, and give `ARMIGER_MODEL_DIR` a writable directory for the HuggingFace download (e.g. `~/armiger-models`).

Building the image:

```bash
buildah bud -t armiger-webhook:latest .
```

## Architecture

The service is a thin adapter with two mapping layers; all ML logic lives in the external armiger repo:

1. **Request mapping** — `build_row()` converts the bitmagnet webhook `torrent` payload into an armiger input row (note the field-name changes: `infoHash` → `infohash`, and `seeders`/`leechers` move into a nested `swarm` object). Malformed payloads raise `BadPayload` → HTTP 400.
2. **Response mapping** — `map_prediction()` converts an `ArmigerUltraPrediction` dict into the webhook response. Key business rules:
   - `delete` is true for: junk, CSAM (`csam_safety.is_safe == False`), honeypot trap, or a failed Mondrian safety gate — **except** when the gate failed only for `out_of_scope` (owner_scope is intentionally treated as "everything" and must never trigger delete).
   - `_delete_reasons` is an internal-only key: populated in `map_prediction`, popped and logged by the handler, stripped before the JSON response is sent.
   - `course` category maps to `ebook` content type; `unknown` yields no `contentType`.
   - baseTitle/year come from the `span_bio` head (falling back to `sanitized_title`); tags come from the adult/anime/english_subs heads.

The pipeline is loaded once (`get_pipeline()`, double-checked singleton) and preloaded at startup in `main()` so failures surface immediately; `/healthz` reports 503 while loading or if loading failed. The server is a `ThreadingHTTPServer` with a 10 MB request-body cap (`MAX_BODY_BYTES`) — keep the body-drain behavior in mind if you change request handling, since unread bodies would otherwise break HTTP/1.1 connection reuse.

`classifier.yml` is the **bitmagnet** classifier workflow (not part of this service's code) that this webhook plugs into: built-in heuristics run first, and the webhook step only fires when `result.contentType` is still unknown. It is mounted into bitmagnet with `webhook.url` pointed at the in-cluster service (`http://armiger.armiger.svc.cluster.local:8096/classify`).
