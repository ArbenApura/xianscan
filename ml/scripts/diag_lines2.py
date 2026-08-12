"""BANDED-MAX PROFILE — THE STRONGEST lines_map VALUE IN EACH COLUMN ACROSS A TEXT BAND."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from app.detect import ComicTextDetector, crop_padded, preprocess_for_onnx
from tests.conftest import make_text_page

det = ComicTextDetector()
det._load()
img = make_text_page()
im_h, im_w = img.shape[:2]
tensor, (dw, dh) = preprocess_for_onnx(img, det.input_size)
out = det._session.run(None, {det._session.get_inputs()[0].name: tensor})
lines = np.squeeze(out[2])
if lines.ndim == 3 and lines.shape[0] > 1:
    lines = lines[0]
lines = crop_padded(lines, dw, dh)
sx, sy = im_w / lines.shape[1], im_h / lines.shape[0]

def band_max(y0, y1, x0, x1, label):
    band = lines[int(y0*sy):int(y1*sy), int(x0*sx):int(x1*sx)]
    mx = band.max(axis=0)
    print(f"--- {label} (band y={y0}..{y1}) ---")
    for i, v in enumerate(mx):
        px = x0 + i * 4
        bar = "#" * int(v * 50)
        print(f"  x={px:4d}  {v:.2f} {bar}")

band_max(100, 170, 90, 370, "LINE 1 你好，世界！")
band_max(295, 375, 85, 440, "LINE 2 系统提示：开始翻译")
