"""DIAGNOSTIC — THE LINES_MAP PROBABILITY PROFILE ALONG THE TEXT ROWS, TO DECIDE THRESHOLDS."""
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
scale_x = im_w / lines.shape[1]
scale_y = im_h / lines.shape[0]
print(f"lines_map {lines.shape}, scale=({scale_x:.2f},{scale_y:.2f})")

def profile(y0, x0, x1, label):
    y = int(y0 * scale_y)
    row = lines[y, int(x0 * scale_x):int(x1 * scale_x)]
    xs = np.arange(int(x0 * scale_x), int(x1 * scale_x))
    # SAMPLE EVERY ~4 MAP PIXELS FOR READABILITY
    print(f"--- {label} (y={y0}) ---")
    for mx, v in zip(xs[::4], row[::4]):
        px = int(mx / scale_x)
        bar = "#" * int(v * 60)
        print(f"  x={px:4d}  {v:.2f} {bar}")

# LINE 1: 你好，世界！ (r0) — x 100..357, y ~106..160
profile(130, 90, 360, "LINE '你好，世界！' (r0)")
# LINE 2: 系统提示：开始翻译 (r1+r2) — x 94..538, y ~301..370
profile(330, 85, 545, "LINE '系统提示：开始翻译' (r1+r2)")
