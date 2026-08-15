# OCR — RAPIDOCR v3 (APACHE-2.0) RECOGNITION ON DETECTOR CROPS, PLUS A FULL-PIPELINE FALLBACK THAT
# DOUBLES AS THE DETECTOR WHEN THE COMIC MODEL ISN'T DOWNLOADED YET.
#
# THE ENGINE IS LAZY-LOADED ONCE PER PROCESS (MODEL FILES ~15MB, AUTO-DOWNLOADED ON FIRST RUN).
# RapidOCR v3 RETURNS A `RapidOCROutput` OBJECT WITH .txts/.scores/.boxes LISTS — NOT THE v1 TUPLE.
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

# -- TYPES -- #


@dataclass
class OcrResult:
	text: str
	score: float
	lines: list[tuple[np.ndarray, str, float]] = field(default_factory=list)


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
				from . import device

				params = device.get_rapidocr_params()
				_engine = RapidOCR(params=params if params else None)
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

	target_h = max(32, int(np.ceil(h / 32.0) * 32))
	target_w = max(32, int(np.ceil(w / 32.0) * 32))
	dh = target_h - h
	dw = target_w - w
	pad_top = dh // 2
	pad_left = dw // 2
	padded = cv2.copyMakeBorder(
		img_bgr, pad_top, dh - pad_top, pad_left, dw - pad_left, cv2.BORDER_CONSTANT, value=[255, 255, 255]
	)
	txts, scores, boxes = _run_engine(padded)
	if not txts:
		txts, scores, boxes = _run_engine(img_bgr)
		pad_top = 0
		pad_left = 0
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

	# DEDUPLICATE SUBSTRINGS / PARTIAL LINE DUPLICATES (e.g. "极高，" INSIDE "极高，称你为……")
	dedup_order: list[int] = []
	for i in order:
		t = str(txts[i]).strip()
		if not t:
			continue
		b = boxes[i] if i < len(boxes) and boxes[i].size else np.zeros((4, 2))
		duplicate = False
		for d_idx in dedup_order:
			dt = str(txts[d_idx]).strip()
			db = boxes[d_idx] if d_idx < len(boxes) and boxes[d_idx].size else np.zeros((4, 2))
			if t in dt or dt in t:
				if b.size and db.size:
					bx = b[:, 0].min()
					by = b[:, 1].min()
					bw = b[:, 0].max() - bx
					bh = b[:, 1].max() - by
					dx = db[:, 0].min()
					dy = db[:, 1].min()
					dw = db[:, 0].max() - dx
					dh = db[:, 1].max() - dy
					ix = max(0.0, min(bx + bw, dx + dw) - max(bx, dx))
					iy = max(0.0, min(by + bh, dy + dh) - max(by, dy))
					inter = ix * iy
					if inter / max(1.0, min(bw * bh, dw * dh)) > 0.40:
						if len(t) <= len(dt):
							duplicate = True
							break
		if not duplicate:
			dedup_order.append(i)

	order = dedup_order
	lines = [str(txts[i]).strip() for i in order if str(txts[i]).strip()]
	if not lines:
		return None

	crop_lines = []
	for i in order:
		t = str(txts[i]).strip()
		if t:
			b = boxes[i] if i < len(boxes) and boxes[i].size else np.zeros((4, 2))
			shifted = b.copy()
			shifted[:, 0] = shifted[:, 0] - pad_left
			shifted[:, 1] = shifted[:, 1] - pad_top
			sc = float(scores[i]) if i < len(scores) else 0.0
			crop_lines.append((shifted, t, sc))

	return OcrResult(text="\n".join(lines), score=max(scores) if scores else 0.0, lines=crop_lines)


def recognize_crop_lines(
	img_bgr: np.ndarray, offset_x: int = 0, offset_y: int = 0
) -> list[tuple[np.ndarray, str, float]]:
	"""OCR A CROP AND RETURN INDIVIDUAL DETECTED LINES WITH GLOBAL PAGE COORDINATES."""
	h, w = img_bgr.shape[:2]
	if h < 8 or w < 8:
		return []

	target_h = max(32, int(np.ceil(h / 32.0) * 32))
	target_w = max(32, int(np.ceil(w / 32.0) * 32))
	dh = target_h - h
	dw = target_w - w
	pad_top = dh // 2
	pad_left = dw // 2
	padded = cv2.copyMakeBorder(
		img_bgr, pad_top, dh - pad_top, pad_left, dw - pad_left, cv2.BORDER_CONSTANT, value=[255, 255, 255]
	)
	txts, scores, boxes = _run_engine(padded)
	if not txts:
		txts, scores, boxes = _run_engine(img_bgr)
		pad_top = 0
		pad_left = 0
	if not txts or not boxes:
		return []

	results = []
	for box, txt, score in zip(boxes, txts, scores):
		t = str(txt).strip()
		if not t:
			continue
		shifted_box = box.copy()
		shifted_box[:, 0] = shifted_box[:, 0] - pad_left + offset_x
		shifted_box[:, 1] = shifted_box[:, 1] - pad_top + offset_y
		results.append((shifted_box, t, float(score)))

	return results


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
