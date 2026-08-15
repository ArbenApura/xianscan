# ORCHESTRATION — THE PURE PIPELINE THE FASTAPI ROUTES CALL. BACKENDS ARE MODULE-LEVEL SINGLETONS SO
# TESTS CAN MONKEYPATCH THEM WITHOUT TOUCHING THE HTTP LAYER.
from __future__ import annotations

import re

import cv2
import numpy as np

from . import detect, ocr
from .inpaint import build_mask, get_inpainter, polygon_from_box
from .schemas import AnalyzeResponse, Box, CleanRequestRegion, Region
from .watermark import watermark_remover

# -- BACKEND SINGLETONS (REPLACEABLE IN TESTS) -- #

detector = detect.ComicTextDetector()

# ELLIPSIS PUNCTUATION — REC MODELS FREQUENTLY READ ONLY PART OF A DOTTED LINE ("......" → "...",
# LEAVING THE REMAINING DOTS ON THE CLEANED PAGE BECAUSE THE INPAINT MASK TRACKS THE OCR BOX).
_ELLIPSIS_TAIL = re.compile(r"[.．…·]{2,}$")
_ALL_ELLIPSIS = re.compile(r"^[.．…·]{2,}$")
_PUNCT_TAIL = re.compile(r"[.．…·!！?？~～]{1,}$")
_EXCLAIM_TAIL = re.compile(r"[!！]$")
_QUESTION_TAIL = re.compile(r"[?？]$")
# A REGION CONTAINING NOTHING BUT 1-2 PUNCTUATION GLYPHS (A LONE "." / "？" / "！").
_PUNCT_ONLY = re.compile(r"^[.．…·!！?？~～]{1,2}$")
_STRAY_DOT_LINE = re.compile(r"^[.．·…]$")
_STRAY_LATIN_SUFFIX = re.compile(r'([\u4e00-\u9fa5]{2,})[a-zA-Z]$')
_TRAILING_CIRCLES_ELLIPSIS = re.compile(r'([\u4e00-\u9fa5])[0oO·•]{2,}$')
_PURE_CIRCLES_ELLIPSIS = re.compile(r'^[0oO·•]{2,}$')


def _clean_stray_ocr_artifacts(text: str) -> str:
	"""STRIP ISOLATED SINGLE LATIN CHARACTERS GLUED TO THE END OF CHINESE PHRASES (e.g. '柳腾e' -> '柳腾')."""
	if not text:
		return text
	lines = text.split('\n')
	cleaned_lines = []
	for line in lines:
		cleaned = line.strip()
		cleaned = _STRAY_LATIN_SUFFIX.sub(r'\1', cleaned)
		cleaned = _TRAILING_CIRCLES_ELLIPSIS.sub(r'\1……', cleaned)
		if _PURE_CIRCLES_ELLIPSIS.fullmatch(cleaned):
			cleaned = "……"
		cleaned_lines.append(cleaned)
	return '\n'.join(cleaned_lines)


def _ellipsis_polygon(base_pts: np.ndarray, union_box: np.ndarray, text: str, page_w: int) -> list[list[int]]:
	"""INPAINT POLYGON FOR (POSSIBLY) TRUNCATED ELLIPSES — HORIZONTAL ONLY, Y-BAND STAYS THE BASE'S.

	1. EXTEND TO THE DETECTOR'S UNION BOX X-EXTENT — IT OFTEN SAW THE WHOLE DOTTED LINE EVEN WHEN
	   THE REC MODEL ONLY READ THE FIRST "..." OF IT.
	2. WHEN THE TEXT IS NOTHING BUT DOTS *AND* THE UNION BOX DIDN'T ALREADY EXTEND MUCH BEYOND THE
	   BASE (i.e. EVERY DETECTOR ONLY SAW THE FIRST DOTS), GROW RIGHTWARD BY 1.2× THE BASE WIDTH
	   (CLAMPED TO THE PAGE) TO REACH THE REST OF THE LINE.
	3. WHEN THE TEXT ENDS IN AN ELLIPSIS, EXTEND RIGHTWARD TO ENSURE FAINT TRAILING DOTS ARE FULLY ENCLOSED.
	"""
	ox, _oy, ow, _oh = detect.box_to_xywh(union_box)
	y0 = float(base_pts[:, 1].min())
	y1 = float(base_pts[:, 1].max())
	x0 = min(float(base_pts[:, 0].min()), float(ox))
	x1 = max(float(base_pts[:, 0].max()), float(ox + ow))
	h = max(1.0, y1 - y0)
	base_w = max(1.0, float(base_pts[:, 0].max() - base_pts[:, 0].min()))
	if _ALL_ELLIPSIS.fullmatch(text) and (x1 - x0) <= base_w * 1.35:
		x1 = min(float(page_w), x1 + base_w * 1.2)
	elif _ELLIPSIS_TAIL.search(text) or text.endswith("……") or text.endswith("..."):
		if (ox + ow) >= float(base_pts[:, 0].max()) + 2.0:
			x1 = min(float(page_w), max(x1, float(ox + ow)))
		else:
			x1 = min(float(page_w), x1 + max(40.0, base_w * 0.28))
	x0 = max(0.0, x0)
	return [[int(x0), int(y0)], [int(x1), int(y0)], [int(x1), int(y1)], [int(x0), int(y1)]]


def _polygon_bounds(polygon: list[list[int]]) -> tuple[int, int, int, int]:
	if not polygon:
		return 0, 0, 1, 1
	x0 = max(0, min(p[0] for p in polygon))
	y0 = max(0, min(p[1] for p in polygon))
	x1 = max(x0 + 1, max(p[0] for p in polygon))
	y1 = max(y0 + 1, max(p[1] for p in polygon))
	return int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0))


def _append_ellipsis(text: str) -> str:
	"""APPEND AN ELLIPSIS UNIT MATCHING THE TEXT'S STYLE — USED WHEN GEOMETRY PROVED MORE DOTS
	THAN THE REC MODEL READ, SO THE EXTRACTED TEXT (AND THEREFORE THE TRANSLATION PROMPT AND THE
	UI) REFLECTS WHAT THE MASK NOW COVERS."""
	tail = text.rstrip()
	if not tail:
		return text
	if tail.endswith("……") or tail.endswith("......"):
		return tail
	if tail.endswith("..."):
		return tail + "..."
	if "…" in tail[-2:]:
		return tail + "…"
	if tail[-1] in ".．·":
		return tail + "..."
	# NO DOTS IN THE TEXT YET (A MASK-GROWN DOTS LINE BELOW) — MATCH THE SCRIPT OF THE TEXT.
	return tail + ("……" if any(ord(c) > 0x2E80 for c in tail) else "...")


def _append_punctuation(text: str) -> str:
	"""APPEND THE MATCHING REPEATED PUNCTUATION GLYPH ('！！' / '？？') ACROSS ALL LINES."""
	lines = text.split("\n")
	res = []
	for line in lines:
		tail = line.rstrip()
		if tail and tail[-1] in "!！?？":
			res.append(tail + tail[-1])
		else:
			res.append(line)
	return "\n".join(res)


def _punct_polygon(hull_pts: np.ndarray, union_box: np.ndarray, page_w: int) -> list[list[int]]:
	"""INPAINT POLYGON FOR A SINGLE-LINE REGION WHOSE TEXT ENDS IN !/? PUNCTUATION.

	EXTEND RIGHT TO THE DETECTOR'S UNION BOX WHEN IT SAW MORE THAN THE OCR HULL. NO BLIND
	GEOMETRIC GROWTH — A COMPLETE SENTENCE ENDING IN "！" (e.g. "你好，世界！") HAS NOTHING
	BEYOND IT, AND GROWING INTO THE BUBBLE WOULD ERASE ART. THE "BIT FAR" MISSED GLYPH CASE IS
	COVERED BY MASK-GUIDED GROWTH (THE DETECTOR'S PROBABILITY MASK PROVIDES REAL EVIDENCE)."""
	ox, _oy, ow, _oh = detect.box_to_xywh(union_box)
	y0 = float(hull_pts[:, 1].min())
	y1 = float(hull_pts[:, 1].max())
	x0 = min(float(hull_pts[:, 0].min()), float(ox))
	x1 = max(float(hull_pts[:, 0].max()), float(ox + ow))
	x0 = max(0.0, x0)
	x1 = min(float(page_w), x1)
	return [[int(x0), int(y0)], [int(x1), int(y0)], [int(x1), int(y1)], [int(x0), int(y1)]]


def _pad_punct_polygon(polygon: list[list[int]], page_w: int) -> list[list[int]]:
	"""PAD A PUNCTUATION-ONLY REGION'S MASK SO A DETECTOR BOX THAT CLIPS THE GLYPH (COMMON FOR A
	LONE "？" — THE BOX IS OFTEN NARROWER THAN THE ACTUAL GLYPH) STILL COVERS IT FULLY."""
	if not polygon:
		return []
	pts = np.asarray(polygon, dtype=np.float64).reshape(-1, 2)
	x0 = float(pts[:, 0].min())
	x1 = float(pts[:, 0].max())
	y0 = float(pts[:, 1].min())
	y1 = float(pts[:, 1].max())
	h = max(1.0, y1 - y0)
	x0 = max(0.0, x0 - h * 0.1)
	x1 = min(float(page_w), x1 + h * 0.35)
	return [[int(x0), int(y0)], [int(x1), int(y0)], [int(x1), int(y1)], [int(x0), int(y1)]]


def _grow_polygon_by_mask(
	polygon: list[list[int]],
	mask: np.ndarray,
	thresh: int = 64,
	dilate_px: int = 24,
) -> list[list[int]] | None:
	"""GROW A REGION'S INPAINT POLYGON TO COVER FAINT TEXT PIXELS THE BOX EXTRACTION MISSED.

	REAL CASE: A SMALL "......" LINE AT THE BOTTOM OF A BUBBLE OFTEN PRODUCES NO BOX AT ALL —
	BUT THE DETECTOR'S TEXT-PROBABILITY MASK STILL HAS SIGNAL THERE. ONLY TEXT PIXELS CONNECTED
	TO THE REGION (WITHIN dilate_px) JOIN THE MASK, SO NEIGHBOURING BUBBLES STAY UNTOUCHED.
	RETURNS None WHEN NOTHING ADJACENT WAS FOUND (CALLER KEEPS THE ORIGINAL POLYGON)."""
	if not polygon or len(polygon) < 3 or mask is None:
		return None
	try:
		pts = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
		seed = np.zeros(mask.shape, dtype=np.uint8)
		cv2.fillPoly(seed, [pts], 255)
		seed = cv2.dilate(seed, np.ones((dilate_px * 2 + 1, dilate_px * 2 + 1), np.uint8))

		text_bin = (mask >= thresh).astype(np.uint8) * 255
		num, labels, _stats, _centroids = cv2.connectedComponentsWithStats(text_bin, connectivity=8)

		touching: set[int] = set()
		for lab in np.unique(labels[seed > 0]):
			if lab > 0:
				touching.add(int(lab))
		if not touching:
			return None

		orig_x0 = float(min(p[0] for p in polygon))
		orig_y0 = float(min(p[1] for p in polygon))
		parts = [pts.reshape(-1, 2).astype(np.float32)]
		for i in sorted(touching):
			ys, xs = np.where(labels == i)
			if xs.size == 0:
				continue
			parts.append(np.stack([xs.astype(np.float32), ys.astype(np.float32)], axis=1))
		hull = cv2.convexHull(np.vstack(parts))
		if hull is None or len(hull) < 3:
			return None
		hull_pts = hull.reshape(-1, 2).astype(np.float64)

		orig_w = float(max(p[0] for p in polygon) - orig_x0)
		orig_h = float(max(p[1] for p in polygon) - orig_y0)
		orig_area = max(1.0, orig_w * orig_h)

		clamped_pts = [[max(int(orig_x0 - 2), int(p[0])), max(int(orig_y0 - 2), int(p[1]))] for p in hull_pts]
		new_x0 = float(min(p[0] for p in clamped_pts))
		new_y0 = float(min(p[1] for p in clamped_pts))
		new_w = float(max(p[0] for p in clamped_pts) - new_x0)
		new_h = float(max(p[1] for p in clamped_pts) - new_y0)
		new_area = new_w * new_h

		# Reject growth that balloons into adjacent elements or giant mask blobs (> 2.0x area or > 1.8x dimension)
		if new_area > 2.0 * orig_area or new_w > 1.8 * max(30.0, orig_w) or new_h > 1.8 * max(30.0, orig_h):
			return None

		return clamped_pts
	except Exception:
		return None



def preprocess_watermark(img_bgr: np.ndarray, corner_margin_pct: float = 0.08) -> np.ndarray:
    """STEP 0: PRE-PROCESS RAW IMAGE ARRAY BY REMOVING WATERMARKS BEFORE OCR."""
    return watermark_remover.process(img_bgr, corner_margin_pct=corner_margin_pct)


def decode_image(data: bytes) -> np.ndarray:
    """DECODE UPLOADED BYTES TO A BGR NUMPY IMAGE; RAISES ValueError ON GARBAGE.
    cv2 (opencv 5.x wheels) CANNOT DECODE AVIF — FALL BACK TO PILLOW (NATIVE AVIF) BEFORE GIVING UP."""
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(data)) as pil:
                rgb = np.array(pil.convert("RGB"))
            img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception:
            img = None
    if img is None:
        raise ValueError("unrecognized image format")
    return img


def _safe_box(x: int | float, y: int | float, w: int | float, h: int | float, page_w: int = 100000, page_h: int = 100000) -> Box:
    cx = max(0, int(x))
    cy = max(0, int(y))
    cw = max(1, int(w))
    ch = max(1, int(h))
    if page_w > 0:
        cx = min(max(0, page_w - 1), cx)
        cw = max(1, min(page_w - cx, cw))
    if page_h > 0:
        cy = min(max(0, page_h - 1), cy)
        ch = max(1, min(page_h - cy, ch))
    return Box(x=cx, y=cy, w=cw, h=ch)


def _region_from_box(box: np.ndarray, index: int, page_w: int, page_h: int) -> Region:
    x, y, w, h = detect.box_to_xywh(box)
    polygon = [[max(0, min(page_w, int(px))), max(0, min(page_h, int(py)))] for px, py in box]
    angle = detect.calculate_box_angle(box)
    return Region(
        id=f"r{index}",
        box=_safe_box(x, y, w, h, page_w, page_h),
        polygon=polygon,
        confidence=0.0,
        vertical=detect.is_vertical_box(box),
        angle=angle,
    )


def _is_multiline_comic_blob(
	cb: np.ndarray,
	rapid_boxes: list[np.ndarray],
	page_h: int = 0,
	page_w: int = 0,
) -> bool:
	"""Check if a ComicTextDetector box is a redundant multi-line or oversized multi-panel blob.
	We drop:
	  1. Oversized blobs spanning across multiple panels (height > 35% page_h and width > 35% page_w, or width >= 70% page_w and height >= 25% page_h and height >= 250).
	  2. Multi-line blobs where height is > 2.8x average line height and height > 160px.
	  3. Blobs where RapidOCR has ALREADY detected 2 or more distinct vertical lines inside it.
	"""
	cx, cy, cw, ch = detect.box_to_xywh(cb)

	# Single vertical text columns (ch > 2.0 * cw) are unified vertical speech bubbles, NOT multi-line horizontal blobs.
	if ch > 2.0 * cw:
		return False

	# 1. OVERSIZED MULTI-PANEL / SCENE BLOB CHECK
	if page_h > 0 and page_w > 0:
		if (ch > 0.35 * page_h and cw > 0.35 * page_w) or (cw >= 0.70 * page_w and ch >= 0.25 * page_h and ch >= 250):
			return True
	elif ch > 380 and cw > 350:
		return True

	overlapping_rapid = []
	for rb in rapid_boxes:
		rx, ry, rw, rh = detect.box_to_xywh(rb)
		ix0 = max(cx, rx)
		iy0 = max(cy, ry)
		ix1 = min(cx + cw, rx + rw)
		iy1 = min(cy + ch, ry + rh)
		if ix1 > ix0 and iy1 > iy0:
			inter_area = (ix1 - ix0) * (iy1 - iy0)
			if inter_area > 0.15 * min(cw * ch, rw * rh):
				overlapping_rapid.append((rx, ry, rw, rh))

	if not overlapping_rapid:
		return False

	# 2. EXCESSIVE HEIGHT RATIO OVER DETECTED TEXT LINES (WHEN 2+ LINES ALREADY DETECTED)
	avg_rh = sum(r[3] for r in overlapping_rapid) / len(overlapping_rapid)
	if len(overlapping_rapid) >= 2 and ch > 2.8 * avg_rh and ch > 160:
		return True

	# 3. SPANS MULTIPLE LINES ALREADY DETECTED (VERTICALLY STACKED OR SIDE-BY-SIDE COLUMNS)
	if len(overlapping_rapid) >= 2:
		for i in range(len(overlapping_rapid)):
			for j in range(i + 1, len(overlapping_rapid)):
				r1 = overlapping_rapid[i]
				r2 = overlapping_rapid[j]
				if abs(r1[1] - r2[1]) > 0.3 * min(r1[3], r2[3]) or abs(r1[0] - r2[0]) > 0.3 * min(r1[2], r2[2]):
					return True

	return False


def _deduplicate_ocr_lines(lines: list[tuple]) -> list[tuple]:
	"""Deduplicate overlapping OCR lines that have identical text or high spatial IoU."""
	if not lines:
		return []
	kept: list[tuple] = []
	for item in lines:
		pts, text, score = item[:3]
		line_ang = item[3] if len(item) > 3 else 0.0
		x, y, w, h = detect.box_to_xywh(pts)
		duplicate = False
		for k_idx, k_item in enumerate(kept):
			k_pts, k_text, k_score = k_item[:3]
			kx, ky, kw, kh = detect.box_to_xywh(k_pts)
			iou = detect.box_iou(pts, k_pts)

			y_overlap = max(0.0, min(float(y + h), float(ky + kh)) - max(float(y), float(ky)))
			min_h = max(1.0, float(min(h, kh)))
			y_overlap_ratio = y_overlap / min_h

			same_text = (
				(text.strip() == k_text.strip())
				or (text.strip() and text.strip() in k_text.strip())
				or (k_text.strip() and k_text.strip() in text.strip())
			)
			if (iou >= 0.65) or (same_text and y_overlap_ratio >= 0.50 and iou >= 0.15):
				if len(text.strip()) > len(k_text.strip()) or (len(text.strip()) == len(k_text.strip()) and score > k_score):
					kept[k_idx] = (pts, text, max(score, k_score), line_ang)
				duplicate = True
				break
		if not duplicate:
			kept.append((pts, text, score, line_ang))
	return kept


def _apply_mask_growth(
	region: Region,
	comic_mask: np.ndarray | None,
	hull_pts: np.ndarray | None,
	matched_count: int,
	box: np.ndarray,
	page_w: int,
	page_h: int = 0,
	other_boxes: list[np.ndarray] | None = None,
	ocr_img: np.ndarray | None = None,
) -> None:
	try:
		if comic_mask is None or not region.polygon or detect._is_watermark_line(region.text):
			return
		orig_polygon = region.polygon
		prev_bottom = max(p[1] for p in orig_polygon)
		prev_right = max(p[0] for p in orig_polygon)

		growth_mask = comic_mask
		if other_boxes:
			growth_mask = comic_mask.copy()
			for ob in other_boxes:
				ox, oy, ow, oh = detect.box_to_xywh(ob)
				growth_mask[max(0, oy) : min(growth_mask.shape[0], oy + oh), max(0, ox) : min(growth_mask.shape[1], ox + ow)] = 0

		grown = _grow_polygon_by_mask(orig_polygon, growth_mask)
		if grown is not None and grown != orig_polygon:
			region.polygon = grown
			bx, by, bw, bh = _polygon_bounds(grown)
			region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
			added_h = max(p[1] for p in grown) - prev_bottom
			added_w = max(p[0] for p in grown) - prev_right
			if hull_pts is not None and matched_count > 0:
				line_h = (hull_pts[:, 1].max() - hull_pts[:, 1].min()) / matched_count
			elif matched_count == 0:
				line_h = float(detect.box_to_xywh(box)[3])
			else:
				line_h = 0.0
			last_char = region.text.rstrip()[-1] if region.text.strip() else ""
			is_terminal = last_char in "。.;；:：!！?？"
			# A SHORT BAND ADDED BELOW THE TEXT IS A MISSED TRAILING LINE OR DOTS LINE
			if not is_terminal and line_h > 0 and 0.35 * line_h <= added_h <= 1.85 * line_h:
				recognized_tail = False
				if ocr_img is not None and page_h > 0 and page_w > 0:
					band_y0 = int(prev_bottom - max(12.0, line_h * 0.45))
					band_y1 = int(max(p[1] for p in grown) + 2)
					band_x0 = int(min(p[0] for p in grown))
					band_x1 = int(max(p[0] for p in grown))
					band_crop = ocr_img[max(0, band_y0):min(page_h, band_y1), max(0, band_x0):min(page_w, band_x1)]
					if band_crop.size > 0:
						rec = ocr.recognize_line(band_crop) or ocr.recognize_crop(band_crop)
						if rec and rec.text.strip() and rec.score >= 0.50:
							tail_t = rec.text.strip()
							if _ELLIPSIS_TAIL.search(tail_t) or _ALL_ELLIPSIS.fullmatch(tail_t) or re.fullmatch(r"^[-—－_.~·.．…\s]+$", tail_t):
								tail_t = re.sub(r"[.．…·\s]{1,}$", "……", tail_t)
								region.text = region.text.rstrip() + "\n" + tail_t
								recognized_tail = True
							elif _QUESTION_TAIL.search(tail_t):
								tail_t = re.sub(r"[?？\s]{1,}$", "？", tail_t)
								region.text = region.text.rstrip() + "\n" + tail_t
								recognized_tail = True
							elif _EXCLAIM_TAIL.search(tail_t):
								tail_t = re.sub(r"[!！\s]{1,}$", "！", tail_t)
								region.text = region.text.rstrip() + "\n" + tail_t
								recognized_tail = True
				if not recognized_tail:
					if _EXCLAIM_TAIL.search(region.text) or "！" in region.text[-4:]:
						unit = "！"
					elif _QUESTION_TAIL.search(region.text) or "？" in region.text[-4:]:
						unit = "？"
					else:
						unit = "……" if any(ord(c) > 0x2E80 for c in region.text) else "..."
					region.text = region.text.rstrip() + "\n" + unit
			elif is_terminal:
				# FOR TERMINAL PUNCTUATION, DO NOT GROW DOWNWARD INTO EMPTY BUBBLE TAILS / ARTWORK
				clamped_grown = [[p[0], min(p[1], prev_bottom)] for p in grown]
				if clamped_grown != orig_polygon:
					region.polygon = clamped_grown
					bx, by, bw, bh = _polygon_bounds(clamped_grown)
					region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
			last_pts = [p for p in orig_polygon if p[1] >= prev_bottom - max(15.0, line_h * 1.2)]
			last_right = max(p[0] for p in last_pts) if last_pts else prev_right
			grown_last_pts = [p for p in grown if p[1] >= prev_bottom - max(15.0, line_h * 1.2)]
			grown_last_right = max(p[0] for p in grown_last_pts) if grown_last_pts else max(p[0] for p in grown)
			added_tail_w = grown_last_right - last_right

			# A REAL HORIZONTAL EXTENSION TO THE RIGHT IS MISSED TRAILING PUNCTUATION OR ELLIPSIS
			if line_h > 0 and (added_w >= max(10.0, line_h * 0.25) or added_tail_w >= max(10.0, line_h * 0.25)):
				if _ELLIPSIS_TAIL.search(region.text):
					region.text = _append_ellipsis(region.text)
					poly_pts = np.asarray(region.polygon, dtype=np.float64)
					widened = _ellipsis_polygon(poly_pts, box, region.text, page_w)
					if widened != region.polygon:
						region.polygon = widened
						bx, by, bw, bh = _polygon_bounds(widened)
						region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
				elif _EXCLAIM_TAIL.search(region.text) or _QUESTION_TAIL.search(region.text):
					region.text = _append_punctuation(region.text)
					poly_pts = np.asarray(region.polygon, dtype=np.float64)
					widened = _punct_polygon(poly_pts, box, page_w)
					if widened != region.polygon:
						region.polygon = widened
						bx, by, bw, bh = _polygon_bounds(widened)
						region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
				elif region.text.strip():
					last_c = region.text.rstrip()[-1]
					if last_c not in "。.;；:：!！?？)]】”’\"'":
						unit = "……" if any(ord(c) > 0x2E80 for c in region.text) else "..."
						region.text = region.text.rstrip() + unit
						poly_pts = np.asarray(region.polygon, dtype=np.float64)
						widened = _ellipsis_polygon(poly_pts, box, region.text, page_w)
						if widened != region.polygon:
							region.polygon = widened
							bx, by, bw, bh = _polygon_bounds(widened)
							region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
					elif last_c in "”’\"'" and ocr_img is not None and page_h > 0 and page_w > 0:
						t_y0 = int(prev_bottom - max(20.0, line_h * 1.3))
						t_y1 = int(prev_bottom + max(10.0, line_h * 0.3))
						t_x0 = int(prev_right - max(15.0, line_h * 0.4))
						t_x1 = min(page_w, int(prev_right + max(80.0, line_h * 2.5)))
						t_crop = ocr_img[max(0, t_y0):min(page_h, t_y1), max(0, t_x0):min(page_w, t_x1)]
						if t_crop.size > 0:
							rec_tail = ocr.recognize_crop(t_crop) or ocr.recognize_line(t_crop)
							if rec_tail and (_ELLIPSIS_TAIL.search(rec_tail.text) or _ALL_ELLIPSIS.fullmatch(rec_tail.text) or "…" in rec_tail.text or ".." in rec_tail.text or "." in rec_tail.text):
								region.text = region.text.rstrip() + "……"
								poly_pts = np.asarray(region.polygon, dtype=np.float64)
								widened = _ellipsis_polygon(poly_pts, box, region.text, page_w)
								if widened != region.polygon:
									region.polygon = widened
									bx, by, bw, bh = _polygon_bounds(widened)
									region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
	except Exception:
		pass



def _is_single_ctd_bubble(line_pts: np.ndarray, comic_boxes: list[np.ndarray]) -> bool:
	"""Check if a ComicTextDetector box covers and encloses this line horizontally."""
	if not comic_boxes:
		return False
	lx, ly, lw, lh = detect.box_to_xywh(line_pts)
	l_area = max(1.0, float(lw * lh))
	for cb in comic_boxes:
		cx, cy, cw, ch = detect.box_to_xywh(cb)
		ix0 = max(lx, cx)
		iy0 = max(ly, cy)
		ix1 = min(lx + lw, cx + cw)
		iy1 = min(ly + lh, cy + ch)
		if ix1 > ix0 and iy1 > iy0:
			inter = (ix1 - ix0) * (iy1 - iy0)
			if (inter / l_area >= 0.60 or cw >= 0.80 * lw) and (ix1 - ix0) >= 0.75 * lw:
				return True
	return False


def _split_lines_by_internal_punctuation(
	rapid_lines: list[tuple],
	ocr_img: np.ndarray,
	comic_boxes: list[np.ndarray] | None = None,
) -> list[tuple]:
	"""SPLIT OCR LINES THAT ACCIDENTALLY FUSE TWO DISTINCT SPEECH BUBBLES / CLAUSES INTO ONE.

	SCENE-TEXT DETECTORS (e.g. PP-OCR DET) OFTEN GROUP TEXT FRAGMENTS FROM ADJACENT SPEECH BUBBLES
	ON THE SAME HORIZONTAL ROW INTO A SINGLE LINE WHEN THEY LIE ON THE SAME ROW
	(e.g. '裤子上不可！哈哈！' SPANNING 2 SPEECH BUBBLES).
	WHEN A LINE HAS INTERNAL SENTENCE-TERMINAL PUNCTUATION (e.g. '！', '。', '？', '…') FOLLOWED BY
	MORE NON-WHITESPACE TEXT (AND BOTH PARTS ARE SUBSTANTIAL UTTERANCES, NOT SHORT INTERJECTIONS LIKE '咦！'),
	WE FIND THE OPTIMAL SPLIT BOUNDARY IN THE IMAGE GAP AND RECOGNIZE EACH SUB-LINE INDIVIDUALLY.
	"""
	if not rapid_lines:
		return rapid_lines

	new_lines: list[tuple] = []
	# Terminal sentence punctuation (!, 。) separating two distinct utterances across a wide bubble span.
	punct_pattern = re.compile(r"([\u3002!！]+)(?=[^\u3002!！?？\s])")

	for item in rapid_lines:
		pts, t, s, ang = item[:4] if len(item) >= 4 else (*item[:3], detect.calculate_box_angle(item[0]))
		text_str = t.strip()
		match = punct_pattern.search(text_str)
		if match:
			# If ComicTextDetector explicitly recognized this as a single unified text bubble, do not split!
			if comic_boxes and _is_single_ctd_bubble(pts, comic_boxes):
				new_lines.append((pts, t, s, ang))
				continue

			split_idx = match.end()
			part1 = text_str[:split_idx].strip()
			part2 = text_str[split_idx:].strip()

			x, y, w, h = detect.box_to_xywh(pts)
			len1 = len(part1)
			len2 = len(part2)
			total_len = len1 + len2

			# Only split wide cross-panel / multi-bubble lines spanning across bubbles (w >= 220 and w > 4.5*h)
			# with substantial clauses on both sides (len1 >= 4, len2 >= 2)
			if total_len > 0 and len1 >= 4 and len2 >= 2 and w >= 220 and w > 4.5 * max(1.0, float(h)):
				prop_x = int(w * (len1 / total_len))
				split_px = prop_x

				b1 = np.array([[x, y], [x + split_px, y], [x + split_px, y + h], [x, y + h]], dtype=np.float64)
				b2 = np.array([[x + split_px, y], [x + w, y], [x + w, y + h], [x + split_px, y + h]], dtype=np.float64)

				new_lines.append((b1, part1, s, detect.calculate_box_angle(b1)))
				new_lines.append((b2, part2, s, detect.calculate_box_angle(b2)))
				continue

		new_lines.append((pts, t, s, ang))

	return new_lines


def _recover_missing_interjection(
	img_bgr: np.ndarray,
	pts: np.ndarray,
	text: str,
) -> str:
	"""Recover missing leading Chinese characters like '诶' in short interjections ('诶！', '诶？', '诶……').

	RapidOCR's vocabulary (ppocr_keys_v1.txt) lacks simplified '诶' (U+8BF6), causing it to emit only
	the trailing punctuation ('！', '？', '……') despite a full-width character box being detected.
	"""
	t_strip = text.strip()
	if t_strip not in ("！", "!", "？", "?", "……", "…", "...", "！？", "!?", "？！", "?!", "呀", "呀！", "呀~"):
		return text
	x, y, w, h = detect.box_to_xywh(pts)
	if w < max(36, int(h * 1.05)) or h < 18:
		return text
	crop = img_bgr[max(0, y) : min(img_bgr.shape[0], y + h), max(0, x) : min(img_bgr.shape[1], x + w)]
	if crop.size == 0 or np.std(crop) < 10.0:
		return text
	gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
	left_part = gray[:, : int(crop.shape[1] * 0.65)]
	if left_part.size == 0:
		return text
	dark_ratio = np.sum(left_part < 140) / float(left_part.size)
	if dark_ratio >= 0.05:
		if t_strip in ("！", "!"):
			return "诶！"
		elif t_strip in ("？", "?"):
			return "诶？"
		elif t_strip in ("……", "…", "..."):
			return "诶……"
		elif t_strip in ("！？", "!?", "？！", "?!"):
			return "诶！？"
		elif t_strip in ("呀", "呀！", "呀~"):
			return "诶呀！"
	return text


def analyze_image(img_bgr: np.ndarray) -> AnalyzeResponse:
	page_h, page_w = img_bgr.shape[:2]
	ocr_img = img_bgr

	# COMIC-TEXT-DETECTOR PRIMARY DETECTION
	comic_boxes: list[np.ndarray] = []
	comic_scores: list[float] = []
	comic_mask: np.ndarray | None = None
	backend: Literal["comic-ctd", "rapidocr-fallback"] = "rapidocr-fallback"

	if detector is not None and detector.available():
		result = detector.analyze(ocr_img)
		comic_boxes = result.boxes
		comic_scores = result.scores
		comic_mask = result.mask
		backend = result.backend

	# RAPIDOCR FULL-PAGE DET+REC — ALWAYS RUN (THE UNION'S SECOND OPINION + TEXT SOURCE)
	rapid_lines = ocr.recognize_full(ocr_img)

	# RECOVER CHINESE TEXT OBSCURED OR COVERED BY COLORED WATERMARK STAMPS (e.g. "点将:" UNDER "COLAMANHUA.com")
	try:
		color_wm = watermark_remover.create_bubble_watermark_mask(ocr_img)
		if np.count_nonzero(color_wm) > 500:
			clean_wm_img = cv2.inpaint(ocr_img, color_wm, 3, cv2.INPAINT_TELEA)
			clean_lines = ocr.recognize_full(clean_wm_img)
			for cpts, ct, cs in clean_lines:
				clean_text = ct.strip()
				if detect._CHINESE_RE.search(clean_text) and not detect._is_watermark_line(clean_text):
					cx, cy, cw, ch = detect.box_to_xywh(cpts)
					replaced = False
					for idx, (rpts, rt, rs) in enumerate(rapid_lines):
						iou = detect.box_iou(cpts, rpts)
						has_latin = bool(re.search(r'[A-Za-z]', rt))
						same_text = (clean_text == rt.strip()) or (clean_text in rt.strip()) or (rt.strip() in clean_text)
						if (has_latin and iou >= 0.35) or (iou >= 0.60) or (same_text and iou >= 0.30):
							if has_latin or len(clean_text) >= len(rt):
								rapid_lines[idx] = (cpts, clean_text, max(cs, rs))
								replaced = True
								break
					if not replaced:
						rapid_lines.append((cpts, clean_text, cs))

			# CLIP RAPIDOCR LINES THAT OVERLAP OR EXTEND INTO THE COLORED WATERMARK STAMP
			clipped_rapid = []
			for pts, t, s in rapid_lines:
				x, y, w, h = detect.box_to_xywh(pts)
				if w > 80 and not detect._CHINESE_RE.search(t):
					line_mask = color_wm[max(0, y):min(page_h, y + h), max(0, x):min(page_w, x + w)]
					col_sums = np.sum(line_mask > 0, axis=0)
					colored_cols = np.where(col_sums > 0.2 * h)[0]
					if colored_cols.size and colored_cols[0] > 30:
						new_w = colored_cols[0]
						pts = np.array([[x, y], [x + new_w, y], [x + new_w, y + h], [x, y + h]], dtype=np.float64)
				clipped_rapid.append((pts, t, s))
			rapid_lines = clipped_rapid
	except Exception:
		pass

	# CLEAN STRAY LATIN NOISE BEFORE ELLIPSIS / PUNCTUATION (e.g. "NMT..." -> "……")
	normalized_rapid_lines = []
	for pts, t, s in rapid_lines:
		line_angle = detect.calculate_box_angle(pts)
		clean_t = t.strip()
		if re.fullmatch(r'^[A-Za-z]{1,4}[.．…!！?？]{1,}$', clean_t):
			has_bang = "!" in clean_t or "！" in clean_t
			has_q = "?" in clean_t or "？" in clean_t
			if has_bang and has_q:
				clean_t = "……！？"
			elif has_bang:
				clean_t = "……！"
			elif has_q:
				clean_t = "……？"
			else:
				clean_t = "……"
		normalized_rapid_lines.append((pts, clean_t, s, line_angle))

	# FILTER OUT OVERSIZED ILLUSTRATION / LOGO ARTWORK BOXES (MASSIVE HEIGHT BUT ONLY A FEW CHARACTERS)
	# GENUINE CHINESE TEXT LINES HAVE SQUARE GLYPHS (w_per_char ≈ height).
	# AN ARTWORK BOX ENCLOSING A GRAPHIC LOGO WITH h >= 100 AND w/len(text) >= 90 AND score < 0.85 IS AN ILLUSTRATION ARTIFACT.
	# ALSO DROP GIANT ARTWORK BOXES (h >= 150 and w >= 250) THAT CONTAIN ONLY A FEW LATIN / NON-CHINESE CHARACTERS.
	clean_rapid_lines = []
	for pts, t, s, line_angle in normalized_rapid_lines:
		_lx, _ly, lw, lh = detect.box_to_xywh(pts)
		char_count = max(1, len(re.sub(r'\s+', '', t)))
		has_chinese = bool(detect._CHINESE_RE.search(t))
		is_giant_artwork = (
			(lh >= 100 and (lw / char_count) >= 90 and s < 0.85)
			or (lh >= 180 and lw >= 350 and not has_chinese)
			or (lh >= 350 and lw >= 350 and char_count <= 6 and not has_chinese)
			or (not has_chinese and lh >= 80 and s < 0.90 and char_count <= 4)
			or (not has_chinese and char_count <= 2 and (lh >= 120 or lw >= 120 or (lh >= 80 and (lh / lw >= 2.5 or lw / lh >= 2.5))))
		)
		if is_giant_artwork:
			continue
		clean_rapid_lines.append((pts, t, s, line_angle))

	# RECOVER CHINESE TEXT FUSED WITH TRAILING/LEADING WATERMARK DOMAINS (e.g. "生活人ugMerge.com" -> "生活人才")
	recovered_rapid_lines = []
	for pts, t, s, line_angle in clean_rapid_lines:
		clean_t = t.strip()
		x, y, w, h = detect.box_to_xywh(pts)
		has_chinese = bool(detect._CHINESE_RE.search(clean_t))
		wm_match = re.search(
			r'[A-Za-z0-9_.-]*(?:Merge|manga|manhua|qumanku|baozimh|colamanga|colamanhua|acloudmerge|oamanhua|nga|\.com|\.net|\.org|\.cc|\.top|\.xyz|\.vip)',
			clean_t,
			re.IGNORECASE,
		)
		if has_chinese and wm_match:
			c_chars = len(detect._CHINESE_RE.findall(clean_t))
			clipped_w = max(1, min(w, int(h * (c_chars + 1.2))))
			crop = ocr.crop_region(
				ocr_img,
				np.array([[x, y], [x + clipped_w, y], [x + clipped_w, y + h], [x, y + h]], dtype=np.float64),
				margin=2,
			)
			crop_res = ocr.recognize_crop(crop)
			if crop_res and detect._CHINESE_RE.search(crop_res.text):
				clean_t = crop_res.text.strip()
				s = max(s, crop_res.score)
			else:
				chinese_part = clean_t[:wm_match.start()].rstrip(" ,.:;-_")
				if chinese_part:
					clean_t = chinese_part + ("，" if ("，" in clean_t or "," in clean_t) and not chinese_part.endswith(("，", ",")) else "")
			pts = np.array([[x, y], [x + clipped_w, y], [x + clipped_w, y + h], [x, y + h]], dtype=np.float64)
			line_angle = detect.calculate_box_angle(pts)
		elif not has_chinese and detect._is_watermark_line(clean_t):
			continue
		clean_t = _recover_missing_interjection(ocr_img, pts, clean_t)
		recovered_rapid_lines.append((pts, clean_t, s, line_angle))
	rapid_lines = recovered_rapid_lines
	rapid_lines = _deduplicate_ocr_lines(rapid_lines)
	rapid_lines = _split_lines_by_internal_punctuation(rapid_lines, ocr_img, comic_boxes=comic_boxes)

	# RECOVER / DISCOVER LINES INSIDE COMIC BOXES THAT FULL-PAGE OCR MISSED (e.g. TITLE LOGOS, HIGH-CONTRAST SFX, SUBTITLES)
	rapid_boxes_xywh = [detect.box_to_xywh(r[0]) for r in rapid_lines]
	current_rapid_boxes = [r[0] for r in rapid_lines]
	for cb in comic_boxes:
		cx, cy, cw, ch = detect.box_to_xywh(cb)
		cb_area = max(1.0, float(cw * ch))
		total_inter = 0.0
		has_enclosed_line = False
		for rx, ry, rw, rh in rapid_boxes_xywh:
			r_area = max(1.0, float(rw * rh))
			ix = max(0, min(cx + cw, rx + rw) - max(cx, rx))
			iy = max(0, min(cy + ch, ry + rh) - max(cy, ry))
			inter = ix * iy
			total_inter += inter
			if inter / r_area >= 0.50:
				has_enclosed_line = True
		# If this comic box is already well covered or encloses an existing text line, skip re-cropping
		if has_enclosed_line or (total_inter / cb_area > 0.30):
			continue

		crop = ocr.crop_region(ocr_img, cb, margin=2)
		c_res = ocr.recognize_crop(crop)
		c_lines = getattr(c_res, "lines", None)
		if c_res and not c_lines and getattr(c_res, "text", None):
			c_lines = [(cb - [cx - 2, cy - 2], c_res.text, c_res.score)]
		if c_res and c_lines:
			offset_x = max(0, cx - 2)
			offset_y = max(0, cy - 2)
			for c_box, c_txt, c_score in c_lines:
				clean_t = c_txt.strip()
				if not clean_t or (len(clean_t) < 2 and not bool(_PUNCT_ONLY.fullmatch(clean_t))) or c_score < 0.60:
					continue
				shifted_box = c_box.copy()
				shifted_box[:, 0] += offset_x
				shifted_box[:, 1] += offset_y
				sx, sy, sw, sh = detect.box_to_xywh(shifted_box)
				# If clean_t is pure punctuation and follows an existing text line on the same row, merge it directly
				merged_tail = False
				if bool(_PUNCT_ONLY.fullmatch(clean_t)):
					for idx, (r_pts, r_txt, r_sc, r_ang) in enumerate(rapid_lines):
						rx, ry, rw, rh = detect.box_to_xywh(r_pts)
						y_overlap = min(sy + sh, ry + rh) - max(sy, ry)
						if y_overlap >= 0.50 * min(sh, rh) and rx - 20 <= sx <= rx + rw + max(50, int(rh * 2.0)):
							m_x1 = max(rx + rw, sx + sw)
							m_y0 = min(ry, sy)
							m_y1 = max(ry + rh, sy + sh)
							merged_box = np.array([[rx, m_y0], [m_x1, m_y0], [m_x1, m_y1], [rx, m_y1]], dtype=np.float64)
							rapid_lines[idx] = (merged_box, r_txt + clean_t, max(r_sc, c_score), r_ang)
							merged_tail = True
							break
				if merged_tail:
					continue

				char_count = max(1, len(re.sub(r'\s+', '', clean_t)))
				has_c_chinese = bool(detect._CHINESE_RE.search(clean_t))
				if (sh >= 180 and sw >= 350 and not has_c_chinese) or (sh >= 350 and sw >= 350 and char_count <= 6 and not has_c_chinese):
					continue
				s_area = max(1.0, sw * sh)
				duplicate = False
				for r_pts, r_txt, _r_sc, _ang in rapid_lines:
					rx, ry, rw, rh = detect.box_to_xywh(r_pts)
					ix = max(0, min(sx + sw, rx + rw) - max(sx, rx))
					iy = max(0, min(sy + sh, ry + rh) - max(sy, ry))
					inter = ix * iy
					if (inter / s_area > 0.45 and (clean_t in r_txt or r_txt in clean_t)) or detect.box_iou(shifted_box, r_pts) > 0.40:
						duplicate = True
						break
				if not duplicate:
					c_ang = detect.calculate_box_angle(shifted_box)
					rapid_lines.append((shifted_box, clean_t, c_score, c_ang))

	rapid_boxes = [pts for pts, _t, _s, _ang in rapid_lines]
	rapid_scores = [float(s) for _pts, _t, s, _ang in rapid_lines]
	rapid_texts = [t for _pts, t, _s, _ang in rapid_lines]

	# FILTER OUT COMIC DETECTOR BLOBS THAT SPAN MULTIPLE VERTICAL LINES.
	# RAPIDOCR PROVIDES PRECISE SINGLE-LINE DETECTIONS; INGESTING OVERLAPPING MULTI-LINE COMIC BLOBS
	# CORRUPTS LINE HEIGHTS AND SPLITS MULTI-LINE BUBBLES INTO OVERLAPPING FRAGMENTS.
	kept_comic_boxes = []
	kept_comic_scores = []
	for cb, cs in zip(comic_boxes, comic_scores):
		if _is_multiline_comic_blob(cb, rapid_boxes, page_h, page_w):
			continue
		cx, cy, cw, ch = detect.box_to_xywh(cb)
		# Skip pure chromatic watermark boxes
		if np.count_nonzero(color_wm) > 500:
			wm_pix = np.sum(color_wm[max(0, cy):min(page_h, cy + ch), max(0, cx):min(page_w, cx + cw)] > 0)
			if wm_pix > 0.20 * max(1, cw * ch):
				continue
		kept_comic_boxes.append(cb)
		kept_comic_scores.append(cs)

	all_boxes = rapid_boxes + kept_comic_boxes
	all_scores = rapid_scores + kept_comic_scores
	all_texts = [t for _pts, t, _s, _ang in rapid_lines] + [""] * len(kept_comic_boxes)
	boxes, _scores = detect.merge_text_lines(all_boxes, all_scores, texts=all_texts)

	# MAP LINE TEXTS TO BOXES FOR WATERMARK / URL SEPARATION DURING PARAGRAPH GROUPING
	box_texts: list[str] = []
	for b in boxes:
		matched_txts = [t for line, t, _sc, _ang in rapid_lines if detect.line_center_inside(line, b)]
		box_texts.append("\n".join(matched_txts))

	# PARAGRAPH GROUPING: A MULTI-LINE BUBBLE'S LINES BECOME ONE REGION (ONE TRANSLATION, ONE BLOCK)
	boxes, _scores = detect.group_paragraphs(boxes, _scores, texts=box_texts)
	# DEDUPLICATE OVERLAPPING PARAGRAPH BOXES FROM DUAL DETECTORS (COMIC + RAPIDOCR)
	boxes, _scores = detect.deduplicate_boxes(boxes, _scores)

	if not boxes:
		return AnalyzeResponse(width=page_w, height=page_h, regions=[], backend=backend)

	order = detect.sort_regions_top_to_bottom(boxes, page_h)
	regions: list[Region] = []
	for i, idx in enumerate(order):
		box = boxes[idx]
		region = _region_from_box(box, i, page_w, page_h)
		# PREFER THE RAPIDOCR LINES' OWN TEXT: ALL LINES WHOSE CENTER LIES INSIDE THIS REGION,
		# ORDERED TOP-TO-BOTTOM (LEFT-TO-RIGHT TIES) AND JOINED — A MULTI-LINE BUBBLE READS AS ONE
		# PARAGRAPH WITH \n BETWEEN LINES.
		matched = [
			(line, text, score, line_ang)
			for line, text, score, line_ang in rapid_lines
			if detect.line_center_inside(line, box)
		]
		hull_pts: np.ndarray | None = None
		if matched:
			# DEDUPLICATE CO-LOCATED LINES MATCHED FROM DUAL PASSES
			unique_matched: list[tuple] = []
			for m in matched:
				m_line, m_text, _m_score, _m_ang = m
				mx, my, mw, mh = detect.box_to_xywh(m_line)
				m_area = max(1.0, float(mw * mh))
				duplicate = False
				for u in unique_matched:
					u_line, u_text, _u_score, _u_ang = u
					ux, uy, uw, uh = detect.box_to_xywh(u_line)
					u_area = max(1.0, float(uw * uh))
					ix = max(0, min(mx + mw, ux + uw) - max(mx, ux))
					iy = max(0, min(my + mh, uy + uh) - max(my, uy))
					inter = ix * iy
					min_a = min(m_area, u_area)
					iou = detect.box_iou(m_line, u_line)
					same_text = (
						(m_text.strip() == u_text.strip())
						or (m_text.strip() and m_text.strip() in u_text.strip())
						or (u_text.strip() and u_text.strip() in m_text.strip())
					)
					if iou >= 0.65 or (same_text and inter / min_a >= 0.50):
						duplicate = True
						break
				if not duplicate:
					unique_matched.append(m)
			matched = unique_matched
			
			# 1. DISCARD NON-CHINESE / ENGLISH SUBTITLE LINES IF CHINESE IS PRESENT
			chinese_matched = [m for m in matched if detect._CHINESE_RE.search(m[1])]
			if chinese_matched:
				matched = chinese_matched
			# 2. DISCARD WATERMARK / SCANLATION SIGNATURE LINES IF LEGITIMATE STORY DIALOGUE IS PRESENT
			story_matched = [m for m in matched if not detect._is_watermark_line(m[1])]
			if story_matched:
				matched = story_matched

			# SORT MATCHED LINES TOP-TO-BOTTOM BEFORE SPLITTING OR MERGING
			matched.sort(key=lambda it: (it[0][:, 1].min(), it[0][:, 0].min()))

			# DISCONNECTED PARAGRAPH SPLIT: IF MATCHED LINES ARE SEPARATED BY LARGE VERTICAL GAPS (> 1.2x LINE HEIGHT),
			# OR ARE SIDE-BY-SIDE IN SEPARATE COLUMNS (MINIMAL X-OVERLAP), OR HAVE EXTREME FONT-SIZE DISPARITY (> 2.2x),
			# THEY BELONG TO DIFFERENT TEXT ELEMENTS (e.g. TITLE vs CHAPTER SUBTITLE) AND MUST BE SPLIT INTO SEPARATE REGIONS.
			sub_groups: list[list[tuple]] = []
			for m in matched:
				m_line = m[0]
				mx, my, mw, mh = detect.box_to_xywh(m_line)
				if not sub_groups:
					sub_groups.append([m])
				else:
					last_m = sub_groups[-1][-1][0]
					lx, ly, lw, lh = detect.box_to_xywh(last_m)
					gap = my - (ly + lh)
					y_overlap = min(my + mh, ly + lh) - max(my, ly)
					x_overlap = min(mx + mw, lx + lw) - max(mx, lx)
					min_h = min(mh, lh)

					# Split if:
					# 1. Large vertical gap (different bubbles across panels)
					# 2. Parallel vertical side-by-side columns (vertical text column with completely disjoint X-ranges: x_overlap <= 0, but overlapping Y-band)
					is_vertical_col = detect.is_vertical_box(last_m) or detect.is_vertical_box(m_line)
					is_single_vertical_bubble = detect.is_vertical_box(box) and x_overlap >= 0.40 * min(mw, lw)
					is_side_by_side = is_vertical_col and (x_overlap <= 0) and (y_overlap > 0.50 * min_h)
					is_disconnected = (not is_single_vertical_bubble and gap > 1.2 * max(mh, lh)) or is_side_by_side
					if is_disconnected:
						sub_groups.append([m])
					else:
						sub_groups[-1].append(m)

			for s_idx, s_matched in enumerate(sub_groups):
				_bx, _by, bw, bh = detect.box_to_xywh(box)
				if detect.is_vertical_box(box):
					raw_parts = []
					for idx, (_l, t, _s, _ang) in enumerate(s_matched):
						m_x, m_y, m_w, m_h = detect.box_to_xywh(_l)
						raw_parts.append(t.strip())
						if idx < len(s_matched) - 1:
							next_l = s_matched[idx + 1][0]
							n_x, n_y, n_w, n_h = detect.box_to_xywh(next_l)
							gap_between = n_y - (m_y + m_h)
							if gap_between >= 0.75 * min(m_h, n_h) and not t.strip().endswith(("…", "...", "·")):
								raw_parts.append("……")
						else:
							gap_below = (_by + bh) - (m_y + m_h)
							if gap_below >= 0.75 * m_h and not t.strip().endswith(("…", "...", "·")):
								raw_parts.append("……")
					raw_text = "\n".join(p for p in raw_parts if p)
				else:
					line_rows: list[list[tuple]] = []
					for m in sorted(s_matched, key=lambda it: (it[0][:, 1].min(), it[0][:, 0].min())):
						if not line_rows:
							line_rows.append([m])
						else:
							last_m = line_rows[-1][-1]
							lx0, ly0, lw, lh = detect.box_to_xywh(last_m[0])
							mx0, my0, mw, mh = detect.box_to_xywh(m[0])
							ly1 = ly0 + lh
							my1 = my0 + mh
							y_overlap = min(ly1, my1) - max(ly0, my0)
							x_overlap = min(lx0 + lw, mx0 + mw) - max(lx0, mx0)
							min_w = min(lw, mw)
							is_same_row = (y_overlap >= 0.65 * min(lh, mh)) and (x_overlap <= 0.35 * min_w)
							if is_same_row:
								line_rows[-1].append(m)
							else:
								line_rows.append([m])
					formatted_lines = []
					for row in line_rows:
						row_sorted = sorted(row, key=lambda it: it[0][:, 0].min())
						row_text = "".join(it[1].strip() for it in row_sorted if it[1].strip())
						if row_text:
							formatted_lines.append(row_text)
					raw_text = "\n".join(formatted_lines)
				cleaned_text = _clean_stray_ocr_artifacts(raw_text)
				s_region = Region(
					id=f"r{len(regions)}",
					box=Box(x=0, y=0, w=1, h=1),
					polygon=[],
					text=cleaned_text,
					confidence=float(max(s for _l, _t, s, _ang in s_matched)) if s_matched else 0.0,
					vertical=False,
					angle=0.0,
				)

				all_pts = np.vstack([line.reshape(-1, 2) for line, _, _, _ in s_matched]).astype(np.float32)
				hull = cv2.convexHull(all_pts)
				hull_pts: np.ndarray | None = None
				if hull is not None and len(hull) >= 3:
					hull_pts = hull.reshape(-1, 2).astype(np.float64)
					_hx, _hy, hw, hh = detect.box_to_xywh(hull_pts)

					# MULTI-LINE / STAT-CARD RESCUE: FOR COMPACT SINGLE-BUBBLE / CARD DETECTIONS WHERE LINES WERE MISSED
					avg_lh = max(1.0, float(hh) / max(1, len(s_matched)))
					is_unclosed_tail = bool(s_region.text.strip() and s_region.text.rstrip()[-1] not in "。.;；:：!！?？")
					needs_rescue = (
						len(sub_groups) == 1
						and not _ALL_ELLIPSIS.fullmatch(s_region.text)
						and not bool(_PUNCT_ONLY.fullmatch(s_region.text))
						and 75 <= bh <= 380
						and (
							(len(s_matched) == 1 and 1.25 * hh <= bh <= 4.0 * hh)
							or (is_unclosed_tail and (bh - hh) >= 0.45 * avg_lh)
							or s_region.confidence < 0.70
							or (detect.is_vertical_box(hull_pts) and s_region.confidence < 0.85)
						)
					)
					if needs_rescue:
						crop_res = ocr.recognize_crop(ocr.crop_region(ocr_img, box, margin=2))
						if crop_res and crop_res.text.strip():
							crop_lines = [cl.strip() for cl in crop_res.text.split("\n") if cl.strip()]
							current_lines = [t.strip() for _l, t, _s, _ang in s_matched if t.strip()]
							if (len(crop_lines) > len(current_lines) and crop_res.score >= 0.70) or (
								crop_res.score > s_region.confidence + 0.15 and crop_res.score >= 0.75
							):
								if getattr(crop_res, "lines", None) and len(crop_res.lines) > 1:
									offset_x = max(0, _bx - 2)
									offset_y = max(0, _by - 2)
									c_groups: list[list[tuple]] = []
									for c_box, c_txt, c_sc in crop_res.lines:
										c_clean = c_txt.strip()
										if not c_clean:
											continue
										c_shifted = c_box.copy()
										c_shifted[:, 0] += offset_x
										c_shifted[:, 1] += offset_y
										c_ang = detect.calculate_box_angle(c_shifted)
										if not c_groups:
											c_groups.append([(c_shifted, c_clean, c_sc, c_ang)])
										else:
											last_c = c_groups[-1][-1][0]
											_lx, _ly, _lw, _lh = detect.box_to_xywh(last_c)
											_cx, _cy, _cw, _ch = detect.box_to_xywh(c_shifted)
											_gap = _cy - (_ly + _lh)
											_x_overlap = min(_cx + _cw, _lx + _lw) - max(_cx, _lx)
											_min_w = min(_cw, _lw)
											_min_h = min(_ch, _lh)
											_grp_cxs = [detect.box_to_xywh(g[0])[0] + detect.box_to_xywh(g[0])[2] / 2.0 for g in c_groups[-1]]
											_grp_mean_cx = sum(_grp_cxs) / len(_grp_cxs)
											_new_cx = _cx + _cw / 2.0
											_is_trailing = (
												(_cw <= int(_lw * 0.70) and _cw <= 70 and _ch <= _lh * 1.75)
												or (0 < len(c_clean) <= 3 and _ch <= _lh * 1.80 and abs(_cx - _lx) <= 0.25 * max(_cw, _lw))
											)
											_is_left_aligned = abs(_cx - _lx) <= 0.25 * _min_w
											_is_right_aligned = abs((_cx + _cw) - (_lx + _lw)) <= 0.25 * _min_w
											_is_shifted = (
												not _is_trailing
												and not _is_left_aligned
												and not _is_right_aligned
												and (
													abs(_new_cx - _grp_mean_cx) > max(40.0, 0.50 * _min_w)
													or (_gap >= 0.20 * _min_h and _x_overlap <= 0.20 * _min_w)
													or (_gap > 1.2 * max(_lh, _ch))
												)
											)
											if _is_shifted:
												c_groups.append([(c_shifted, c_clean, c_sc, c_ang)])
											else:
												c_groups[-1].append((c_shifted, c_clean, c_sc, c_ang))
									for cg_matched in c_groups:
										cg_pts = np.vstack([l.reshape(-1, 2) for l, _t, _s, _a in cg_matched]).astype(np.float32)
										cg_hull = cv2.convexHull(cg_pts)
										cg_poly = cg_hull.reshape(-1, 2).astype(np.float64) if cg_hull is not None else box
										cghx, cghy, cghw, cghh = detect.box_to_xywh(cg_poly)
										cg_clean_t = _clean_stray_ocr_artifacts("\n".join(t for _l, t, _s, _a in cg_matched if t.strip()))
										cg_reg = Region(
											id=f"r{len(regions)}",
											box=_safe_box(cghx, cghy, max(1, cghw), max(1, cghh), page_w, page_h),
											polygon=[[int(p[0]), int(p[1])] for p in cg_poly],
											text=cg_clean_t,
											confidence=float(max(s for _l, _t, s, _a in cg_matched)),
											vertical=detect.is_vertical_box(cg_poly),
											angle=float(np.median([a for _l, _t, _s, a in cg_matched])) if cg_matched else 0.0,
										)
										regions.append(cg_reg)
									continue
								else:
									s_region.text = crop_res.text.strip()
									s_region.confidence = max(s_region.confidence, crop_res.score)
									s_region.polygon = [[int(px), int(py)] for px, py in box]
									s_region.box = _safe_box(_bx, _by, bw, bh, page_w, page_h)
									s_region.vertical = detect.is_vertical_box(box)
									line_angles = [line_ang for _l, _t, _s, line_ang in s_matched]
									if line_angles:
										med = float(np.median(line_angles))
										s_region.angle = 0.0 if abs(med) < 2.5 else round(med, 2)
									else:
										s_region.angle = 0.0
									regions.append(s_region)
									continue

					s_region.polygon = [[int(p[0]), int(p[1])] for p in hull_pts]
					if len(s_matched) == 1 and _PUNCT_TAIL.search(s_region.text):
						is_dots = bool(_ELLIPSIS_TAIL.search(s_region.text))
						punct_only = bool(_PUNCT_ONLY.fullmatch(s_region.text))
						widened = (
							_ellipsis_polygon(hull_pts, box, s_region.text, page_w)
							if is_dots
							else _punct_polygon(hull_pts, box, page_w)
						)
						if punct_only:
							widened = _pad_punct_polygon(widened, page_w)
						if widened != s_region.polygon:
							hull_right = float(hull_pts[:, 0].max())
							hull_h = max(1.0, float(hull_pts[:, 1].max() - hull_pts[:, 1].min()))
							widened_right = float(max(p[0] for p in widened))
							s_region.polygon = widened
							if is_dots:
								s_region.text = _append_ellipsis(s_region.text)
							elif not punct_only and widened_right - hull_right >= hull_h * 0.35:
								s_region.text = _append_punctuation(s_region.text)
						bx, by, bw, bh = _polygon_bounds(s_region.polygon)
						s_region.box = _safe_box(bx, by, bw, bh, page_w, page_h)
					elif detect.is_vertical_box(box) and bh >= 2.0 * bw:
						s_region.polygon = [[int(px), int(py)] for px, py in box]
						s_region.box = _safe_box(_bx, _by, bw, bh, page_w, page_h)
					else:
						# REDERIVE BOX FROM THE HULL'S BOUNDING BOX
						hx, hy, hw, hh = detect.box_to_xywh(hull_pts)
						s_region.box = _safe_box(hx, hy, max(1, hw), max(1, hh), page_w, page_h)
					s_region.vertical = detect.is_vertical_box(box if (detect.is_vertical_box(box) and bh >= 2.0 * bw) else hull_pts)
				else:
					s_region.polygon = [[int(px), int(py)] for px, py in box]
					s_region.box = _safe_box(_bx, _by, bw, bh, page_w, page_h)
					s_region.vertical = detect.is_vertical_box(box)

				# COMPUTE ORIENTATION ANGLE FROM MATCHED OCR LINE ANGLES (PARAGRAPH MEDIAN ACROSS ALL LINES)
				line_angles = [line_ang for _l, _t, _s, line_ang in s_matched]
				if line_angles:
					med = float(np.median(line_angles))
					s_region.angle = 0.0 if abs(med) < 2.5 else round(med, 2)
				else:
					s_region.angle = 0.0

				# MASK-GUIDED GROWTH FOR s_region (EXCLUDING OTHER LINES TO PREVENT INVADING NEIGHBOURING BUBBLES)
				other_lines = [l[0] for l in rapid_lines if not any(detect.box_iou(l[0], sm[0]) > 0.50 for sm in s_matched)]
				_apply_mask_growth(s_region, comic_mask, hull_pts, len(s_matched), box, page_w, page_h, other_boxes=other_lines, ocr_img=ocr_img)
				regions.append(s_region)
		else:
			crop = ocr.crop_region(ocr_img, box, margin=2)
			bx, by, bw, bh = detect.box_to_xywh(box)
			ocr_result = ocr.recognize_crop(crop)
			if ocr_result and getattr(ocr_result, "lines", None) and len(ocr_result.lines) > 1:
				offset_x = max(0, bx - 2)
				offset_y = max(0, by - 2)
				crop_sub_groups: list[list[tuple]] = []
				for c_box, c_txt, c_sc in ocr_result.lines:
					shifted_box = c_box.copy()
					shifted_box[:, 0] += offset_x
					shifted_box[:, 1] += offset_y
					c_ang = detect.calculate_box_angle(shifted_box)
					m = (shifted_box, c_txt, c_sc, c_ang)
					if not crop_sub_groups:
						crop_sub_groups.append([m])
					else:
						last_m = crop_sub_groups[-1][-1][0]
						lx, ly, lw, lh = detect.box_to_xywh(last_m)
						mx, my, mw, mh = detect.box_to_xywh(shifted_box)
						gap = my - (ly + lh)
						y_overlap = min(my + mh, ly + lh) - max(my, ly)
						x_overlap = min(mx + mw, lx + lw) - max(mx, lx)
						min_h = min(mh, lh)
						is_vertical_col = detect.is_vertical_box(last_m) or detect.is_vertical_box(shifted_box)
						is_side_by_side = is_vertical_col and (x_overlap <= 0) and (y_overlap > 0.50 * min_h)
						if gap > 1.2 * max(mh, lh) or is_side_by_side:
							crop_sub_groups.append([m])
						else:
							crop_sub_groups[-1].append(m)

				for s_matched in crop_sub_groups:
					raw_t = "\n".join(t for _l, t, _s, _a in s_matched if t.strip())
					clean_t = _clean_stray_ocr_artifacts(raw_t)
					sub_pts = np.vstack([l.reshape(-1, 2) for l, _, _, _ in s_matched]).astype(np.float32)
					sub_hull = cv2.convexHull(sub_pts)
					sub_poly = (
						sub_hull.reshape(-1, 2).astype(np.float64)
						if sub_hull is not None and len(sub_hull) >= 3
						else sub_pts.reshape(-1, 2).astype(np.float64)
					)
					shx, shy, shw, shh = detect.box_to_xywh(sub_poly)
					sub_reg = Region(
						id=f"r{len(regions)}",
						box=_safe_box(shx, shy, max(1, shw), max(1, shh), page_w, page_h),
						polygon=[[int(p[0]), int(p[1])] for p in sub_poly],
						text=clean_t,
						confidence=float(max(s for _l, _t, s, _a in s_matched)),
						vertical=detect.is_vertical_box(sub_poly),
						angle=float(np.median([a for _l, _t, _s, a in s_matched])) if s_matched else 0.0,
					)
					other_lines = [l[0] for l in rapid_lines if detect.box_iou(l[0], sub_poly) <= 0.50]
					_apply_mask_growth(sub_reg, comic_mask, sub_poly, len(s_matched), box, page_w, page_h, other_boxes=other_lines, ocr_img=ocr_img)
					regions.append(sub_reg)
			elif ocr_result:
				region.text = _clean_stray_ocr_artifacts(ocr_result.text)
				region.confidence = ocr_result.score
				region.angle = detect.calculate_box_angle(box)
				# TRAILING-PUNCTUATION RECOVERY (CROP PATH) — SAME RULES AS THE MATCHED PATH.
				if _PUNCT_TAIL.search(ocr_result.text):
					is_dots = bool(_ELLIPSIS_TAIL.search(ocr_result.text))
					punct_only = bool(_PUNCT_ONLY.fullmatch(ocr_result.text))
					widened = (
						_ellipsis_polygon(box, box, ocr_result.text, page_w)
						if is_dots
						else _punct_polygon(box, box, page_w)
					)
					if punct_only:
						widened = _pad_punct_polygon(widened, page_w)
					if widened != region.polygon:
						base_right = float(max(p[0] for p in region.polygon))
						base_h = max(1.0, float(detect.box_to_xywh(box)[3]))
						widened_right = float(max(p[0] for p in widened))
						region.polygon = widened
						if is_dots:
							region.text = _append_ellipsis(region.text)
						elif not punct_only and widened_right - base_right >= base_h * 0.35:
							region.text = _append_punctuation(region.text)
					bx, by, bw, bh = _polygon_bounds(region.polygon)
					region.box = _safe_box(bx, by, bw, bh, page_w, page_h)

				# MASK-GUIDED GROWTH FOR crop region
				other_lines = [l[0] for l in rapid_lines if detect.box_iou(l[0], box) <= 0.50]
				_apply_mask_growth(region, comic_mask, None, 0, box, page_w, page_h, other_boxes=other_lines, ocr_img=ocr_img)
				regions.append(region)

	# -- STRAY-DOT / EXCLAMATION CLEANUP:
	# 1. REC MODELS SOMETIMES SPLIT A FINAL "..." INTO THE LAST WORD PLUS A SEPARATE LONE-DOT LINE
	# 2. REC MODELS SOMETIMES MISCLASSIFY A STANDALONE "！" AS "1", "I", "|", OR "i" IN EXCLAMATION BUBBLES
	_STRAY_DOT_LINE = re.compile(r"^[.．·…]$")
	for region in regions:
		lines = region.text.split("\n")
		if len(lines) >= 2 and _STRAY_DOT_LINE.fullmatch(lines[-1].strip()):
			region.text = "\n".join(lines[:-2] + [lines[-2].rstrip() + lines[-1].strip()])
			lines = region.text.split("\n")
		if len(lines) >= 2:
			fixed_lines = []
			for idx, l in enumerate(lines):
				l_str = l.strip()
				if l_str in ("1", "|", "I", "i", "l") and any("！" in prev or "!" in prev or "啊" in prev for prev in lines[:idx]):
					fixed_lines.append("！")
				else:
					fixed_lines.append(l)
			region.text = "\n".join(fixed_lines)
		# CLEAN STRAY LATIN NOISE BEFORE ELLIPSIS / PUNCTUATION (e.g. "NMT........." -> "……！" or "……")
		if re.fullmatch(r'^[A-Za-z]{1,4}[.．…!！?？]{2,}$', region.text.strip()):
			has_bang = "!" in region.text or "！" in region.text
			has_q = "?" in region.text or "？" in region.text
			if has_bang and has_q:
				region.text = "……！？"
			elif has_bang:
				region.text = "……！"
			elif has_q:
				region.text = "……？"
			else:
				region.text = "……"
		r_pts = np.array(
			[
				[region.box.x, region.box.y],
				[region.box.x + region.box.w, region.box.y],
				[region.box.x + region.box.w, region.box.y + region.box.h],
				[region.box.x, region.box.y + region.box.h],
			],
			dtype=np.float64,
		)
		region.text = _recover_missing_interjection(ocr_img, r_pts, region.text)

	# 2) A PUNCTUATION-ONLY REGION MERGES INTO ITS NEIGHBOUR: THE LONE "." UNDER "JINGZHOU" AND
	# THE LONE "？" AFTER "穿越者！" MUST JOIN THE ADJACENT TEXT — NEVER STAND ALONE.
	final_regions: list[Region] = []
	for region in regions:
		if final_regions and _PUNCT_ONLY.fullmatch(region.text.strip()):
			prev = final_regions[-1]
			prev_lines = [l for l in prev.text.split("\n") if l.strip()]
			prev_line_count = max(1, len(prev_lines))
			est_line_h = max(12.0, float(prev.box.h) / prev_line_count)

			# A genuine lone punctuation mark (., ?, !) is compact (not a wide watermark banner or logo)
			is_compact_punct = region.box.w <= max(60, int(est_line_h * 2.5)) and region.box.h <= max(80, int(est_line_h * 2.5))

			# VERTICAL: THE PUNCTUATION SITS BELOW THE TEXT (X-RANGES OVERLAP, GAP WITHIN FEW LINE-HEIGHTS —
			# BUBBLES OFTEN HAVE WHITESPACE BETWEEN THE LAST WORD AND A TRAILING ".").
			# NEVER MERGE IF THE GAP IS ACROSS PANELS (MAX GAP SCALED BY LINE HEIGHT, NOT MULTI-LINE PARAGRAPH HEIGHT)
			# OR IF THE PREVIOUS TEXT ALREADY HAS TERMINAL PUNCTUATION AND THE CANDIDATE IS ANOTHER DUPLICATE TERMINAL MARK.
			v_gap = region.box.y - (prev.box.y + prev.box.h)
			x_overlap = min(region.box.x + region.box.w, prev.box.x + prev.box.w) - max(region.box.x, prev.box.x)
			already_terminated = prev.text.rstrip().endswith(("！", "!", "。", "？", "?", "…"))
			is_duplicate_terminal = already_terminated and region.text.strip() in "！!。.？?"
			vert_ok = (
				is_compact_punct
				and not is_duplicate_terminal
				and 0 <= v_gap <= max(est_line_h * 5.0, 180.0)
				and x_overlap >= min(region.box.w, prev.box.w) * 0.2
			)
			# HORIZONTAL: THE PUNCTUATION SITS RIGHT OF THE TEXT ON THE SAME LINE (e.g. THE "？"
			# OF "穿越者！？" — OFTEN A BIT FAR FROM THE EXCLAMATION).
			h_gap = region.box.x - (prev.box.x + prev.box.w)
			y_overlap = min(region.box.y + region.box.h, prev.box.y + prev.box.h) - max(region.box.y, prev.box.y)
			horiz_ok = is_compact_punct and 0.0 <= h_gap <= est_line_h * 2.5 and y_overlap >= region.box.h * 0.5
			if vert_ok or horiz_ok:
				p_text = prev.text.rstrip()
				r_text = region.text.strip()
				if r_text:
					if p_text.endswith("！！") and r_text in "？?":
						p_text = p_text[:-1]
					elif p_text.endswith("？？") and r_text in "！!":
						p_text = p_text[:-1]
					prev.text = p_text + r_text
				if prev.polygon and region.polygon:
					joined = np.vstack(
						[
							np.asarray(prev.polygon, dtype=np.float64).reshape(-1, 2),
							np.asarray(region.polygon, dtype=np.float64).reshape(-1, 2),
						]
					).astype(np.float32)
					hull = cv2.convexHull(joined)
					if hull is not None and len(hull) >= 3:
						hull_pts = hull.reshape(-1, 2).astype(np.float64)
						prev.polygon = [[int(p[0]), int(p[1])] for p in hull_pts]
					bx, by, bw, bh = _polygon_bounds(prev.polygon)
					prev.box = _safe_box(bx, by, bw, bh, page_w, page_h)
				continue
		final_regions.append(region)

	# 3) FINAL NMS DEDUPLICATION: REMOVE DUPLICATE SUBSET BOXES (e.g. WHEN A SINGLE LINE FROM A
	# DUAL DETECTOR IS FULLY ENCLOSED INSIDE AN ALREADY-GROUPED MULTI-LINE PARAGRAPH REGION).
	if len(final_regions) > 1:
		sorted_regs = sorted(final_regions, key=lambda r: (len(r.text), r.box.w * r.box.h), reverse=True)
		kept_final: list[Region] = []
		for r in sorted_regs:
			x0, y0, w, h = r.box.x, r.box.y, r.box.w, r.box.h
			area = w * h
			duplicate = False
			for k in kept_final:
				# NEVER LET A WATERMARK OR SCANLATION REGION SUPPRESS A LEGITIMATE STORY DIALOGUE REGION
				if detect._is_watermark_line(k.text) != detect._is_watermark_line(r.text):
					continue
				kx0, ky0, kw, kh = k.box.x, k.box.y, k.box.w, k.box.h
				karea = kw * kh
				ix = max(0, min(x0 + w, kx0 + kw) - max(x0, kx0))
				iy = max(0, min(y0 + h, ky0 + kh) - max(y0, ky0))
				inter = ix * iy
				min_area = min(area, karea)
				overlap = inter / min_area if min_area > 0 else 0
				if overlap >= 0.65 or (r.text in k.text and overlap >= 0.40):
					duplicate = True
					break
			if not duplicate:
				kept_final.append(r)
		kept_final.sort(key=lambda r: (r.box.y, r.box.x))
		final_regions = kept_final

	# 4) DISCARD EMPTY, DASH-ONLY NOISE, LOW CONFIDENCE (< 0.55), AND STANDALONE WATERMARK REGIONS (e.g. "速漫库", "qumanku.com")
	_IGNORED_NOISE_RE = re.compile(r"^[—―\-_~～\s]*$")
	filtered_regions = []
	for r in final_regions:
		t_strip = r.text.strip()
		has_c = bool(detect._CHINESE_RE.search(t_strip))
		c_count = len(re.sub(r"\s+", "", t_strip))
		is_punct = bool(_PUNCT_ONLY.fullmatch(t_strip) or _ALL_ELLIPSIS.fullmatch(t_strip))
		is_stray_non_chinese = not has_c and not is_punct and (
			(c_count <= 2 and (r.box.h >= 120 or r.box.w >= 120 or (r.box.h >= 80 and (r.box.h / max(1, r.box.w) >= 2.5 or r.box.w / max(1, r.box.h) >= 2.5))))
			or (c_count <= 1 and (bool(re.fullmatch(r"[a-zA-Z]", t_strip)) or r.confidence < 0.75))
			or (c_count <= 4 and bool(re.fullmatch(r"^[0oO·•]+$", t_strip)) and r.box.w <= 70 and r.box.h <= 70)
		)
		if not t_strip or _IGNORED_NOISE_RE.fullmatch(t_strip) or detect.is_pure_watermark_region(t_strip) or is_stray_non_chinese:
			continue
		if _PUNCT_ONLY.fullmatch(t_strip) and r.box.w >= 80:
			continue
		if r.confidence < 0.55:
			continue
		filtered_regions.append(r)
	final_regions = filtered_regions

	return AnalyzeResponse(
		width=page_w,
		height=page_h,
		regions=final_regions,
		backend=backend,
	)


def clean_image(img_bgr: np.ndarray, regions: list[CleanRequestRegion]) -> np.ndarray:
    """ERASE THE ORIGINAL TEXT: FILL THE REGION POLYGONS (BOX FALLBACK) AND INPAINT."""
    page_h, page_w = img_bgr.shape[:2]
    polygons = []
    for r in regions:
        if len(r.polygon) >= 3:
            polygons.append(np.array(r.polygon, dtype=np.float64))
        else:
            polygons.append(polygon_from_box(r.box.x, r.box.y, r.box.w, r.box.h))
    mask = build_mask(page_h, page_w, polygons)
    if not mask.any():
        return img_bgr
    return get_inpainter()(img_bgr, mask)


def encode_png(img_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError("failed to encode output image")
    return buf.tobytes()



def stitch_vertical_images(img_top: np.ndarray, img_bottom: np.ndarray) -> np.ndarray:
    """STITCH TWO BGR NUMPY IMAGES VERTICALLY.
    IF WIDTHS DIFFER, RESIZE THE BOTTOM IMAGE TO MATCH THE TOP IMAGE'S WIDTH.
    """
    top_h, top_w = img_top.shape[:2]
    bot_h, bot_w = img_bottom.shape[:2]

    if bot_w != top_w:
        new_bot_h = max(1, int(bot_h * (top_w / bot_w)))
        img_bottom = cv2.resize(img_bottom, (top_w, new_bot_h), interpolation=cv2.INTER_AREA)

    return np.vstack([img_top, img_bottom])


