"""COMPARE RAPIDOCR'S DETECTOR AGAINST THE COMIC DETECTOR ON THE SAME PAGE."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from app.detect import ComicTextDetector
from app.ocr import _get_engine
from tests.conftest import make_text_page

img = make_text_page()
det = ComicTextDetector()
result = det.analyze(img)
print("comic-ctd boxes:")
for b, s in zip(result.boxes, result.scores):
    x = b[:, 0]; y = b[:, 1]
    print(f"  x={int(x.min())}..{int(x.max())} y={int(y.min())}..{int(y.max())} score={s:.2f}")

out = _get_engine()(img)
print("rapidocr det boxes:")
for b, t, s in zip(out.boxes, out.txts, out.scores):
    pts = np.array(b)
    print(f"  x={int(pts[:,0].min())}..{int(pts[:,0].max())} y={int(pts[:,1].min())}..{int(pts[:,1].max())} score={float(s):.2f} text={t!r}")
