# Manua Translator

Self-hosted Chinese manhua → English translation pipeline.

- `web/` — SvelteKit app: UI, glossary, DeepSeek translation pipeline, job orchestration (TypeScript, vitest TDD).
- `ml/` — Python FastAPI sidecar: text detection + OCR + inpainting (CPU / ONNX).

See `web/README.md` for setup and usage.
