#!/usr/bin/env python3
"""Armiger bitmagnet webhook classifier service.

Implements the bitmagnet classifier webhook contract
(noldslop/bitmagnet internal/classifier/action_webhook.go):

  POST /classify  -> JSON response with matched/delete/contentType/baseTitle/year/languages/tags
  GET  /healthz   -> {"status":"ok"}, or 503 when the pipeline failed to load

The service loads the Armiger Ultra pipeline (ONNX 8-head backbone + span_bio
ONNX + zero-cue catalog + swarm survival net) from ARMIGER_MODEL_DIR and
classifies torrents on demand. It uses only the Python standard library for
HTTP (no web framework), so the container stays small and dependency-light.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Make the armiger modules importable (flat module layout at the repo root).
# ---------------------------------------------------------------------------
MODEL_DIR = os.environ.get("ARMIGER_MODEL_DIR", "/app/armiger")
if MODEL_DIR not in sys.path:
    sys.path.insert(0, MODEL_DIR)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("armiger-webhook")

# Lazy import so import-time errors surface at startup, not at module load.
_pipeline = None
_pipeline_lock = threading.Lock()
_pipeline_error: str | None = None


def get_pipeline():
    """Load (once) and return the ArmigerUltraPipeline singleton."""
    global _pipeline, _pipeline_error
    if _pipeline is None and _pipeline_error is None:
        with _pipeline_lock:
            if _pipeline is None and _pipeline_error is None:
                try:
                    from armiger_ultra_pipeline import ArmigerUltraPipeline

                    log.info("Loading ArmigerUltraPipeline from %s ...", MODEL_DIR)
                    _pipeline = ArmigerUltraPipeline.from_directory(
                        MODEL_DIR, variant="ultra", preload_catalog=True
                    )
                    log.info("Pipeline loaded successfully.")
                except Exception as exc:  # noqa: BLE001
                    log.exception("Failed to load pipeline")
                    _pipeline_error = str(exc)
    if _pipeline_error is not None:
        raise RuntimeError(f"pipeline unavailable: {_pipeline_error}")
    return _pipeline


# ---------------------------------------------------------------------------
# Mapping helpers: armiger prediction -> bitmagnet webhook response
# ---------------------------------------------------------------------------

# armiger category -> bitmagnet content type
CATEGORY_TO_CONTENT_TYPE = {
    "movie": "movie",
    "tv_show": "tv_show",
    "xxx": "xxx",
    "music": "music",
    "comic": "comic",
    "game": "game",
    "audiobook": "audiobook",
    "software": "software",
    "ebook": "ebook",
    "course": "ebook",
    "unknown": None,
}

# armiger lang -> bitmagnet language codes
LANG_TO_CODES = {
    "english": ["en"],
    "non_english": [],
    "multi_or_unknown": [],
}


class BadPayload(ValueError):
    """The request payload is malformed (client error, HTTP 400)."""


def _as_int(value, field: str) -> int:
    """Coerce a JSON field to int, raising BadPayload for non-numbers."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadPayload(f"{field} must be a number")


def build_row(torrent: dict) -> dict:
    """Map a bitmagnet webhook torrent payload to an armiger input row.

    Raises BadPayload when the payload shape is malformed.
    """
    if not isinstance(torrent, dict):
        raise BadPayload("torrent must be an object")
    raw_files = torrent.get("files") or []
    if not isinstance(raw_files, list):
        raise BadPayload("torrent.files must be a list")
    files = []
    for i, f in enumerate(raw_files):
        if not isinstance(f, dict):
            raise BadPayload(f"torrent.files[{i}] must be an object")
        files.append(
            {
                "path": f.get("path") or "",
                "size": _as_int(f.get("size"), f"torrent.files[{i}].size"),
            }
        )
    swarm = torrent.get("swarm") or {}
    if not isinstance(swarm, dict):
        raise BadPayload("torrent.swarm must be an object")
    return {
        "name": str(torrent.get("name") or ""),
        "size": _as_int(torrent.get("size"), "torrent.size"),
        "infohash": str(torrent.get("infoHash") or ""),
        "files": files,
        "swarm": {
            "seeders": _as_int(swarm.get("seeders"), "torrent.swarm.seeders"),
            "leechers": _as_int(swarm.get("leechers"), "torrent.swarm.leechers"),
        },
    }


def map_prediction(pred: dict) -> dict:
    """Map an ArmigerUltraPrediction dict to a bitmagnet webhook response."""
    resp: dict = {"matched": True}

    # --- delete decision -------------------------------------------------
    delete = False
    if pred.get("junk") == "junk":
        delete = True
    csam = pred.get("csam_safety") or {}
    if csam.get("overall_safe_to_index") is False:
        delete = True
    honeypot = pred.get("honeypot_trap") or {}
    if honeypot.get("is_honeypot_trap") is True:
        delete = True
    gates = pred.get("mondrian_gates") or {}
    if gates.get("safety_gate_passed") is False:
        delete = True
    resp["delete"] = delete

    # --- content type ----------------------------------------------------
    category = pred.get("category", "unknown")
    ct = CATEGORY_TO_CONTENT_TYPE.get(category)
    if ct:
        resp["contentType"] = ct

    # --- base title + year (from span_bio head) --------------------------
    heads = pred.get("heads") or {}
    span_bio = heads.get("span_bio") or {}
    title = span_bio.get("title") or pred.get("sanitized_title")
    if title:
        resp["baseTitle"] = title
    year = span_bio.get("year")
    if year:
        try:
            resp["year"] = int(year)
        except (TypeError, ValueError):
            pass

    # --- languages -------------------------------------------------------
    lang = pred.get("lang", "multi_or_unknown")
    codes = LANG_TO_CODES.get(lang, [])
    if codes:
        resp["languages"] = codes

    # --- tags ------------------------------------------------------------
    tags = []
    if pred.get("adult") == "adult":
        tags.append("adult")
    if pred.get("anime") == "anime":
        tags.append("anime")
    if pred.get("english_subs") == "english_subs":
        tags.append("english_subs")
    if tags:
        resp["tags"] = tags

    return resp


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


# Cap request bodies so a single bad request cannot exhaust pod memory.
MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


class Handler(BaseHTTPRequestHandler):
    server_version = "armiger-webhook/1.0"
    protocol_version = "HTTP/1.1"

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self) -> None:
        """Discard an unread request body so the connection can be reused."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            return
        while length > 0:
            chunk = self.rfile.read(min(65536, length))
            if not chunk:
                break
            length -= len(chunk)

    def _read_body(self) -> bytes:
        """Read the request body, enforcing MAX_BODY_BYTES.

        Raises BadPayload if it exceeds the limit; the excess is drained so
        the connection stays usable after the 400.
        """
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            raise BadPayload("invalid Content-Length")
        if length > MAX_BODY_BYTES:
            self._drain_body()
            raise BadPayload(f"body exceeds {MAX_BODY_BYTES} bytes")
        if length == 0:
            return b"{}"
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            if _pipeline_error is not None:
                self._send_json(
                    {"status": "error", "error": _pipeline_error}, 503
                )
            elif _pipeline is None:
                self._send_json({"status": "loading"}, 503)
            else:
                self._send_json(
                    {
                        "status": "ok",
                        "model_dir": MODEL_DIR,
                        "pipeline_loaded": _pipeline is not None,
                    }
                )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/classify":
            self._drain_body()
            self._send_json({"error": "not found"}, 404)
            return
        try:
            raw = self._read_body()
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise BadPayload("request body must be a JSON object")
            row = build_row(payload.get("torrent") or {})
        except BadPayload as exc:
            self._send_json({"error": f"bad request: {exc}"}, 400)
            return
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"bad request: invalid JSON ({exc})"}, 400)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("request processing failed")
            self._send_json({"error": str(exc)}, 400)
            return

        try:
            pipeline = get_pipeline()
            pred = pipeline.classify(row)
            resp = map_prediction(pred)
            self._send_json(resp)
        except Exception as exc:  # noqa: BLE001
            log.exception("classify failed")
            self._send_json({"error": str(exc)}, 500)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    port = int(os.environ.get("PORT", "8096"))
    # Preload the pipeline at startup so the first request is fast and any
    # model-loading problem surfaces immediately (healthz reports it).
    try:
        get_pipeline()
    except Exception as exc:  # noqa: BLE001
        log.error("Pipeline preload failed: %s", exc)
        # Continue serving so /healthz can report the error state.
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("armiger-webhook listening on :%d (model_dir=%s)", port, MODEL_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
