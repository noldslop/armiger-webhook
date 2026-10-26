# armiger-webhook

Bitmagnet webhook classifier service built on [Armiger Ultra](https://github.com/spencernorton/armiger).

Armiger is a torrent-content classifier: an ONNX 8-head neural backbone (category,
adult, junk, anime, english_subs, lang, owner_scope, title_language) plus a span_bio
ONNX head (base title + year), a zero-cue catalog, a torch-based swarm survival net
(honeypot detection), and a CSAM safety guard. This repo wraps it as an HTTP service
that speaks the **bitmagnet classifier webhook** contract.

## Endpoints

| Method | Path        | Description                                        |
|--------|-------------|----------------------------------------------------|
| POST   | `/classify` | Classify a torrent; returns the webhook response   |
| GET    | `/healthz`  | Health check (`{"status":"ok"}`)                   |

### Request (`POST /classify`)

```json
{
  "torrent": {
    "name": "Some.Movie.2024.1080p.BluRay.x264",
    "size": 1234567890,
    "infoHash": "abc123...",
    "seeders": 42,
    "leechers": 3,
    "files": [
      {"path": "Some.Movie.2024.1080p.BluRay.x264.mkv", "size": 1234567890}
    ]
  }
}
```

### Response

```json
{
  "matched": true,
  "delete": false,
  "contentType": "movie",
  "baseTitle": "Some Movie",
  "year": 2024,
  "languages": ["en"],
  "tags": ["english_subs"]
}
```

`delete` is `true` when the torrent is junk, fails the CSAM safety guard, or is
flagged as a honeypot trap. `contentType` maps Armiger categories to bitmagnet
content types (`movie`, `tv_show`, `xxx`, `music`, `comic`, `game`, `audiobook`,
`software`, `ebook`).

## Building

The image is built with buildah (or any OCI builder). It uses uv to install the
Python dependencies (torch CPU-only, onnxruntime, numpy, cryptography) and clones
Armiger at a pinned commit. The container runs as the unprivileged `app` user
(UID 65532, the `nobody` user on Debian).

```bash
buildah bud -t armiger-webhook:latest .
```

To build from a different Armiger commit:

```bash
buildah bud --build-arg ARMIGER_REF=<sha-or-branch> -t armiger-webhook:latest .
```

## Wiring into bitmagnet

`classifier.yml` in this repo is the bitmagnet classifier workflow. It is the core
workflow with one added step: when the built-in heuristics can't determine a content
type, it calls this service's `/classify` endpoint. Mount it into bitmagnet and point
the `webhook.url` at this service's `/classify` address.
