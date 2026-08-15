# MANU-TRANSLATOR ML SIDECAR — CONFIGURATION
#
# ALL PATHS / FLAGS ARE ENV-OVERRIDABLE SO THE SIDECAR RUNS UNCHANGED ON ANY MACHINE:
#   MT_MODELS_DIR   where download_models.py put the weights (default ./models relative to this file)
#   MT_DETECT_SIZE  ONNX detector input size (default 1024 — do not change without retraining)
#   MT_DEVICE       onnxruntime execution provider hint (cpu | coreml | cuda) — cpu is the safe default
from __future__ import annotations

import os
from pathlib import Path

MODELS_DIR = Path(os.environ.get("MT_MODELS_DIR", Path(__file__).resolve().parent.parent / "models"))

DETECT_MODEL_PATH = MODELS_DIR / "comictextdetector.pt.onnx"
LAMA_MODEL_PATH = MODELS_DIR / "lama.onnx"

# THE ONNX DETECTOR'S TRAINED INPUT SIZE (LETTERBOXED TO A SQUARE)
DETECT_INPUT_SIZE = int(os.environ.get("MT_DETECT_SIZE", "1024"))

from . import device

# ORT PROVIDERS — DYNAMIC AUTO-DETECTION (CUDA, DIRECTML, COREML, CPU) WITH OPTIONAL MT_DEVICE OVERRIDE
ORT_PROVIDERS = device.get_ort_providers()
DEVICE_LABEL = device.get_device_label()
create_session = device.create_inference_session

# MINIMUM REGION HEIGHT/WIDTH AFTER UNCLIP (PIXELS, IN THE *MODEL* COORDINATE SPACE) — FILTERS NOISE
MIN_REGION_SIDE = 5

# DB POSTPROCESSING DEFAULTS (PINNED TO manga-image-translator's ctd.py VALUES)
DB_THRESH = 0.3
DB_BOX_THRESH = 0.6
DB_UNCLIP_RATIO = 1.5
DB_MAX_CANDIDATES = 1000

# LaMa INFERENCE TILE — THE MODEL RUNS ON 256×256 PATCHES; LARGER MASKS ARE TILED (MEMORY-FRIENDLY ON CPU)
LAMA_PATCH = 256
