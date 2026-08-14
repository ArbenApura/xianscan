# OCR — RAPIDOCR v3 (APACHE-2.0) RECOGNITION ON DETECTOR CROPS, PLUS A FULL-PIPELINE FALLBACK THAT
# DOUBLES AS THE DETECTOR WHEN THE COMIC MODEL ISN'T DOWNLOADED YET.
#
# THE ENGINE IS LAZY-LOADED ONCE PER PROCESS (MODEL FILES ~15MB, AUTO-DOWNLOADED ON FIRST RUN).
# RapidOCR v3 RETURNS A `RapidOCROutput` OBJECT WITH .txts/.scores/.boxes LISTS — NOT THE v1 TUPLE.
from __future__ import annotations

import threading
from dataclasses import dataclass

import cv2
import numpy as np

# -- TYPES -- #


@dataclass
class OcrResult:
	text: str
	score: float


# -- ENGINE -- #

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
	global _engine
	if _engine is None:
		# DOUBLE-CHECKED LOCKING — CONCURRENT /pages/analyze CALLS (THREADPOOL ENDPOINTS) MUST NOT
		# CONSTRUCT TWO RapidOCR ENGINES (DUPLICATE ~15MB MODEL LOAD + A RACE ON THE SINGLETON).
		with _engine_lock:
			if _engine is None:
				from rapidocr import RapidOCR

				_engine = RapidOCR()
	return _engine


def _run_engine(img_bgr: np.ndarray, use_det: bool = True) -> tuple[list[str], list[float], list[np.ndarray]]:
	"""RUN DET+REC, NORMALISING THE v3 OUTPUT OBJECT TO PLAIN LISTS."""
	out = _get_engine()(img_bgr, use_det=use_det)
	txts = list(getattr(out, "txts", None) or [])
	scores = [float(s) for s in (getattr(out, "scores", None) or [])]
	boxes_raw = getattr(out, "boxes", None)
	boxes = []
	if boxes_raw is not None:
		arr = np.asarray(boxes_raw)
		if arr.size:
			boxes = [np.array(b, dtype=np.float64) for b in arr]
	return txts, scores, boxes


def recognize_line(img_bgr: np.ndarray) -> OcrResult | None:
	"""RUN DIRECT TEXT RECOGNITION (NO DETECTION STEP) ON A TIGHT LINE CROP."""
	h, w = img_bgr.shape[:2]
	if h < 4 or w < 4:
		return None
	txts, scores, _ = _run_engine(img_bgr, use_det=False)
	if not txts or not str(txts[0]).strip():
		return None
	return OcrResult(text=str(txts[0]).strip(), score=float(scores[0]) if scores else 0.0)


def recognize_crop(img_bgr: np.ndarray) -> OcrResult | None:
	"""OCR ONE REGION CROP. RETURNS None WHEN THE CROP IS TOO SMALL OR THE ENGINE FINDS NO TEXT.

	ALL LINES IN THE CROP ARE RETURNED, ORDERED TOP-TO-BOTTOM (LEFT-TO-RIGHT TIES) AND JOINED
	WITH \n — A MULTI-LINE BUBBLE CROP READS AS ONE PARAGRAPH.
	"""
	h, w = img_bgr.shape[:2]
	if h < 8 or w < 8:
		return None
	txts, scores, boxes = _run_engine(img_bgr)
	if not txts:
		return None
	# ORDER BY (TOP EDGE, LEFT EDGE) — READING ORDER WITHIN THE CROP
	order = sorted(
		range(len(txts)),
		key=lambda i: (
			boxes[i][:, 1].min() if i < len(boxes) and boxes[i].size else 0.0,
			boxes[i][:, 0].min() if i < len(boxes) and boxes[i].size else 0.0,
		),
	)
	lines = [str(txts[i]).strip() for i in order if str(txts[i]).strip()]
	if not lines:
		return None
	return OcrResult(text="\n".join(lines), score=max(scores) if scores else 0.0)


def recognize_full(img_bgr: np.ndarray) -> list[tuple[np.ndarray, str, float]]:
	"""FULL-PAGE DET+REC (FALLBACK BACKEND): [(4-POINT BOX IN ORIGINAL PIXELS, TEXT, SCORE), ...]."""
	txts, scores, boxes = _run_engine(img_bgr)
	out = []
	for box, text, score in zip(boxes, txts, scores):
		t = str(text).strip()
		if t:
			out.append((box, t, float(score)))
	return out


def crop_region(img_bgr: np.ndarray, polygon: np.ndarray, margin: int = 2) -> np.ndarray:
	"""CROP A REGION'S AXIS-ALIGNED BOUNDS FROM THE PAGE, WITH A SMALL MARGIN."""
	h, w = img_bgr.shape[:2]
	xs = polygon[:, 0]
	ys = polygon[:, 1]
	x0 = max(0, int(xs.min()) - margin)
	y0 = max(0, int(ys.min()) - margin)
	x1 = min(w, int(xs.max()) + margin)
	y1 = min(h, int(ys.max()) + margin)
	if x1 - x0 < 4 or y1 - y0 < 4:
		return img_bgr[y0 : max(y0 + 1, y1), x0 : max(x0 + 1, x1)]
	return img_bgr[y0:y1, x0:x1]
