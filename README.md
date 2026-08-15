<div align="center">

# 🏮 Xianscan (仙Scan)

**Self-Hosted Comic & Manhua Translation Pipeline**

*End-to-end automated text detection, optical character recognition, DeepSeek translation with glossary consistency, AI inpainting, and studio-grade typesetting.*

<br/>

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 20+](https://img.shields.io/badge/Node.js-20+-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![SvelteKit](https://img.shields.io/badge/SvelteKit-2.x-FF3E00?style=for-the-badge&logo=svelte&logoColor=white)](https://kit.svelte.dev/)
[![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-1.19+-005CED?style=for-the-badge&logo=onnx&logoColor=white)](https://onnxruntime.ai/)
[![Hardware Support](https://img.shields.io/badge/Hardware-CPU_•_DirectML_•_CUDA_•_Apple_Silicon-success?style=for-the-badge)](https://github.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

</div>

---

## 📖 Overview

**Xianscan** is an open-source, self-hosted web application and ML sidecar designed to translate raw Chinese, Japanese, and Korean webtoons, manhua, and manga into polished English with natural dialogue flow and authentic comic book typography.

Upload raw chapter images, and the system automatically orchestrates the entire pipeline:
**Detection (ComicTextDetector) ➔ OCR (RapidOCR) ➔ Translation (DeepSeek V4 + Glossary) ➔ Inpainting (LaMa) ➔ Canvas Typesetting (Skia / OFL Fonts)**.

```
Browser (SvelteKit UI)
   │  SSE progress streaming + REST API
   ▼
SvelteKit Server (web/)
   ├─ glossary.ts / glossary-match.ts   — Aho-Corasick trie matching + prompt injection
   ├─ translate.ts / deepseek.ts        — DeepSeek V4 client (queued, retried, usage-tracked)
   ├─ chapter-pipeline.ts               — Parallel runner: analyze ➔ translate ➔ clean ➔ typeset
   ├─ typeset.ts                        — @napi-rs/canvas rendering with CC Wild Words & OFL fonts
   └─ SQLite (Drizzle ORM)              — books / chapters / pages / regions / glossary / cache
   │
   ▼  HTTP (ML_BASE_URL)
Python ML Sidecar (ml/) — FastAPI + ONNX Runtime (CPU / DirectML / CUDA / CoreML)
   ├─ /pages/analyze   — ComicTextDetector + RapidOCR ➔ bounding boxes, polygons, text lines
   ├─ /pages/clean     — Big-LaMa ONNX inpainting ➔ text-erased background artwork
   └─ /pages/preprocess— Intelligent header/footer logo & watermark filtering
```

---

## 🪶 Lightweight & Portable by Design

Our primary engineering goal is to make Xianscan **as lightweight, portable, and accessible as possible**:

- **🚫 No Heavy PyTorch or CUDA Bloat**: The entire machine learning backend runs on lightweight **ONNX Runtime**. You do not need to download 5GB+ PyTorch binaries or install massive CUDA Toolkits.
- **📦 Ultra-Compact Disk Footprint**: Total model weight downloads are **under ~600 MB** combined (ComicTextDetector + RapidOCR + LaMa ONNX).
- **🗄️ Self-Contained Local Database**: Powered by a zero-configuration, single-file **SQLite** database with automatic Drizzle migrations. No external database servers (PostgreSQL/MySQL) or Docker containers required.
- **💻 Run Anywhere**: Engineered to run smoothly on standard non-GPU budget laptops, Intel/AMD mini-PCs, MacBooks, or home servers without special hardware requirements.

---

## ✨ Key Features

- **⚡ Universal Hardware Acceleration & 100% CPU Compatibility**:
  - **Works on any PC out of the box with pure CPU** — no expensive dedicated GPU required.
  - **Auto-Accelerated**: Automatically harnesses AMD Radeon (iGPU & dGPU), Intel Arc/Iris/Xe, NVIDIA CUDA, or Apple Silicon when present.
  - **Self-Healing Fallback**: If GPU memory or driver issues occur, the pipeline silently falls back to CPU without failing translation jobs.
- **🎯 Precision Speech Bubble Detection & OCR**:
  - Combines **ComicTextDetector (CTD)** for bubble segmentation and polygon bounding with **RapidOCR (PP-OCRv4)** for multi-line text reading.
  - Intelligent polygon mask growth recovers faint trailing dots (`……`), scream marks (`！`), and multiline speech bubbles.
- **🤖 Context-Aware DeepSeek LLM Translation**:
  - Powered by **DeepSeek V4 (Flash / Pro)** with custom system prompts tailored for manhua martial-arts terms, honorifics, and narrative tone.
  - **Aho-Corasick Dynamic Glossary Matching**: Injects character names, cultivation ranks, and faction terminology to guarantee cross-chapter naming consistency.
  - Supports book-scoped and global-scoped terms with CSV import/export.
- **🎨 High-Fidelity AI Inpainting**:
  - Uses the **Big-LaMa ONNX** inpainting network to cleanly remove original text and restore background artwork with zero ghosting.
- **✍️ Studio-Grade Canvas Typesetting**:
  - Automatically formats text using the standard **CC Wild Words** comic typeface.
  - Features dynamic font-size fitting, line-height balancing, hyphenation avoidance, and Skia glyph fallbacks for em-dashes and special punctuation.
- **📱 Reading & QC Modes**:
  - **Webtoon Mode**: Seamless vertical infinite scroll.
  - **Comparison Mode**: Real-time side-by-side view with slider.
  - **Grid & Inspector Mode**: Review detected text boxes, edit translations manually, and export finished chapters as ZIP archives.

---

## ⚡ Quick Start (1-Click)

### 🪟 Windows (Recommended)
Simply double-click `start.bat` in the project root:
```bat
start.bat
```
> **What this does automatically:**
> 1. Creates a Python virtual environment in `ml/.venv`.
> 2. Automatically installs GPU/DirectML acceleration for AMD, Intel, and NVIDIA graphics (or CPU fallback).
> 3. Downloads required model weights (`comictextdetector` and `lama.onnx`).
> 4. Installs web dependencies and launches both the ML backend and Web UI.

### 🐧 Linux / 🍎 macOS
```bash
chmod +x start.sh
./start.sh
```

---

## 🛠️ Manual Installation (Developers)

### 1. Prerequisites
- **Node.js**: v20+ (Node 22 or 24 recommended)
- **Python**: v3.10+ (Python 3.11 – 3.14 supported)
- **DeepSeek API Key** *(Optional for local testing; required for live LLM translation)*

### 2. Setup Python ML Backend
```bash
# Navigate to ml directory
cd ml

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Linux / macOS: source .venv/bin/activate

# Install dependencies (auto-detects DirectML on Windows, CPU on Linux/Mac)
pip install -r requirements.txt

# Download model weights (~600MB)
python scripts/download_models.py

# Start ML sidecar
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

### 3. Setup Web Application
```bash
# In a separate terminal, navigate to web directory
cd web

# Install node dependencies
npm install

# Configure environment variables
cp .env.example .env

# Edit .env and set your DeepSeek API key:
# DEEPSEEK_API_KEY=sk-your-key-here

# Run database migrations (SQLite)
npm run db:migrate

# Start web dev server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser!

---

## 🖥️ Hardware Acceleration Matrix

Xianscan is built with **zero-configuration hardware auto-detection**. You never have to manually edit configuration files to select your GPU.

| Environment | Supported Hardware | Acceleration Engine | Performance |
| :--- | :--- | :--- | :--- |
| **Any Computer** | Standard Intel / AMD CPU *(Baseline)* | Multi-threaded CPU | ~2.0 – 3.5s / page |
| **Windows PC / Laptop** | AMD Radeon iGPU / dGPU, Intel Arc/Iris, NVIDIA | **DirectX 12 (DirectML)** | **~0.3 – 0.6s / page** *(5x–8x faster)* |
| **NVIDIA Systems** | RTX 20/30/40/50 Series, GTX 1660 | **CUDA / TensorRT** | **~0.15 – 0.3s / page** *(10x–15x faster)* |
| **Apple Silicon** | M1 / M2 / M3 / M4 (Air, Pro, Max) | **CoreML / Metal** | **~0.2 – 0.5s / page** *(8x–12x faster)* |

> 💡 **Pure CPU Friendly**: If you don't have a GPU, Xianscan will run smoothly on pure CPU using optimized ONNX thread pools.

---

## ⚙️ Configuration & Environment Variables

### Web Backend (`web/.env`)
| Variable | Default | Description |
| :--- | :--- | :--- |
| `DEEPSEEK_API_KEY` | *None* | DeepSeek API key for LLM translations. |
| `DEEPSEEK_BASE_URL`| `https://api.deepseek.com` | Custom OpenAI-compatible LLM endpoint. |
| `DEEPSEEK_MODEL`   | `deepseek-v4-flash` | Translation model (`deepseek-v4-flash` or `deepseek-v4-pro`). |
| `ML_BASE_URL`      | `http://127.0.0.1:8001` | URL of the Python ML sidecar service. |
| `DATABASE_URL`     | `data/manua.db` | SQLite database storage path. |
| `PIPELINE_PAGE_CONCURRENCY` | `3` | Number of pages processed in parallel per pipeline phase. |

> 💡 **Testing without an API Key**: Set `DEEPSEEK_BASE_URL=http://127.0.0.1:8010` and run `node scripts/mock-llm.mjs` in the `web/` directory for a local offline fake translator.

### ML Backend (`ml/.env` or OS Environment)
| Variable | Default | Description |
| :--- | :--- | :--- |
| `MT_DEVICE` | *auto* | Force execution provider: `auto`, `cpu`, `dml`, `cuda`, `coreml`. |
| `MT_MODELS_DIR` | `ml/models` | Path where model weights are stored. |
| `MT_DETECT_SIZE` | `1024` | Input letterbox resolution for ComicTextDetector. |

---

## 🧪 Testing & Validation

The codebase includes an extensive automated test suite with mock fixtures and real-page regression samples:

```bash
# Run Web unit & integration tests (212+ tests via Vitest)
cd web
npm test

# Run Python ML test suite (108+ tests via PyTest)
cd ml
.venv/Scripts/python -m pytest ml/tests/test_api.py ml/tests/test_inpaint.py ml/tests/test_detect.py
```

---

## 📜 Licenses & Acknowledgments

All bundled code in this repository is licensed under the **[MIT License](LICENSE)** (Copyright © 2026 Arben Apura).

Upstream AI models, weights, and tools are acknowledged under their respective open-source licenses:
- **ComicTextDetector**: Model weights and text detection architecture adapted from [manga-image-translator](https://github.com/zyddnys/manga-image-translator) (GPL-3.0). Model weights are downloaded dynamically at runtime.
- **RapidOCR Engine**: PP-OCRv4 ONNX models and inference by [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0).
- **LaMa Inpainting**: Large Mask Inpainting model by [advimman/lama](https://github.com/advimman/lama) & [Sanster/models](https://github.com/Sanster/models) (Apache-2.0).
- **Comic Fonts**: CC Wild Words & Friendly Sans under the Open Font License (OFL-1.1).
