# Manua Translator — web app

Self-hosted Chinese manhua → English translation. Upload chapter images, and the pipeline
**detects text (comic-text-detector ONNX) → OCRs it (RapidOCR) → translates it (DeepSeek, with your
glossary) → erases the original text (LaMa) → typesets the translation (Skia)**. A per-page
before/after viewer and a zip download are included; the glossary editor is on `/app/glossary`.

## Architecture

```
Browser (SvelteKit UI)
   │  SSE progress + REST
   ▼
SvelteKit server (web/)
   ├─ glossary.ts / glossary-match.ts   — Aho-Corasick matching + prompt injection
   ├─ translate.ts / deepseek.ts        — DeepSeek calls (queued, retried, usage-tracked)
   ├─ chapter-pipeline.ts               — per-page: analyze → translate → clean → typeset
   ├─ typeset.ts                        — @napi-rs/canvas rendering (bundled OFL fonts)
   └─ SQLite (Drizzle)                  — books / chapters / pages / regions / glossary / cache
   │
   ▼  HTTP (ML_BASE_URL)
Python sidecar (ml/) — FastAPI + ONNX Runtime, CPU
   ├─ /pages/analyze   — comic-text-detector + RapidOCR → regions (boxes, polygons, text)
   └─ /pages/clean     — LaMa (or cv2 fallback) inpainting → text-erased page
```

All ML runs on CPU (ONNX Runtime). The sidecar falls back gracefully: no comic model → RapidOCR
detection; no torch/LaMa weights → cv2 inpainting.

## Setup

Prerequisites: **Node 24+**, **Python 3.11+** (3.14 works), ~600 MB of model downloads.

```bash
# 1) WEB APP
cd web
npm install
cp .env.example .env        # then set DEEPSEEK_API_KEY (real translations)
npm run db:migrate          # creates data/manua.db (migrations auto-run at boot too)

# 2) ML SIDECAR (in a second terminal)
cd ../ml
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows; on Unix: .venv/bin/pip
.venv/Scripts/pip install torch                      # optional but recommended (LaMa)
.venv/Scripts/python scripts/download_models.py      # ~300 MB: detector + big-lama + rapidocr

# 3) RUN
cd ../ml && .venv/Scripts/python -m uvicorn app.main:app --port 8001   # sidecar
cd ../web && npm run dev                                                 # app → http://localhost:5173
```

`.env` (web/): `ML_BASE_URL=http://127.0.0.1:8001`, `DEEPSEEK_API_KEY=sk-…`,
`DEEPSEEK_MODEL=deepseek-v4-flash` (or `-pro`), `DATA_ROOT=./data` (optional).

No API key? Set `DEEPSEEK_BASE_URL=http://127.0.0.1:8010` and run
`node scripts/mock-llm.mjs` for a local fake translator — the full pipeline works end to end.

## Usage

1. `/app/` → create a book → create a chapter → upload page images (PNG/JPEG/WebP).
2. Hit **Translate** — progress streams over SSE; every page goes through the pipeline.
   **Force re-run** aborts and restarts (the translation cache makes re-runs cheap).
3. Each done page toggles original ↔ translated; **Download zip** exports the chapter.
4. `/app/glossary` — manage terms (book + global scopes, aliases, genders, CSV import/export).
   Matched terms are injected into every translation prompt; book-scope terms override global ones.

Failure behaviour: a bad page marks itself `error` (with the message) and the job continues; a
sidecar outage produces a clear `ML sidecar unreachable` error; aborting (force re-run) stops the
old job cleanly. Jobs survive client disconnects (buffered events) and re-runs after a restart
(resets stuck `processing` pages).

## Tests

```bash
cd web && npm test                  # vitest — 128+ tests, in-memory SQLite, fake LLM/sidecar
cd ml && .venv/Scripts/python -m pytest   # sidecar unit tests (no models needed)
cd ml && .venv/Scripts/python scripts/verify_models.py   # real-model smoke test → verify-out/
```

The web suite is the TDD core: glossary (ported from xianslate), DeepSeek client/retry/cost math,
prompt construction with a fake LLM, job lifecycle, cache fingerprinting, the chapter runner with a
fake sidecar + fake LLM, typesetting geometry, SSE parsing, and the HTTP client contract.

## Licences

All components are Apache-2.0-compatible: manga-image-translator (detector weights),
RapidOCR (Apache-2.0 models), LaMa big-lama (Sanster/models), fonts (OFL). The code in this repo
is adapted from the author's earlier project (xianslate) and the open-source pieces cited above.
