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


def _ellipsis_polygon(base_pts: np.ndarray, union_box: np.ndarray, text: str, page_w: int) -> list[list[int]]:
	"""INPAINT POLYGON FOR (POSSIBLY) TRUNCATED ELLIPSES — HORIZONTAL ONLY, Y-BAND STAYS THE BASE'S.

	1. EXTEND TO THE DETECTOR'S UNION BOX X-EXTENT — IT OFTEN SAW THE WHOLE DOTTED LINE EVEN WHEN
	   THE REC MODEL ONLY READ THE FIRST "..." OF IT.
	2. WHEN THE TEXT IS NOTHING BUT DOTS *AND* THE UNION BOX DIDN'T ALREADY EXTEND MUCH BEYOND THE
	   BASE (i.e. EVERY DETECTOR ONLY SAW THE FIRST DOTS), GROW RIGHTWARD BY 1.2× THE BASE WIDTH
	   (CLAMPED TO THE PAGE) TO REACH THE REST OF THE LINE.
	"""
	ox, _oy, ow, _oh = detect.box_to_xywh(union_box)
	y0 = float(base_pts[:, 1].min())
	y1 = float(base_pts[:, 1].max())
	x0 = min(float(base_pts[:, 0].min()), float(ox))
	x1 = max(float(base_pts[:, 0].max()), float(ox + ow))
	base_w = max(1.0, float(base_pts[:, 0].max() - base_pts[:, 0].min()))
	if _ALL_ELLIPSIS.fullmatch(text) and (x1 - x0) <= base_w * 1.35:
		x1 = min(float(page_w), x1 + base_w * 1.2)
	x0 = max(0.0, x0)
	return [[int(x0), int(y0)], [int(x1), int(y0)], [int(x1), int(y1)], [int(x0), int(y1)]]


def _polygon_bounds(polygon: list[list[int]]) -> tuple[int, int, int, int]:
	x0 = min(p[0] for p in polygon)
	y0 = min(p[1] for p in polygon)
	x1 = max(p[0] for p in polygon)
	y1 = max(p[1] for p in polygon)
	return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


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
	dilate_px: int = 35,
) -> list[list[int]] | None:
	"""GROW A REGION'S INPAINT POLYGON TO COVER FAINT TEXT PIXELS THE BOX EXTRACTION MISSED.

	REAL CASE: A SMALL "......" LINE AT THE BOTTOM OF A BUBBLE OFTEN PRODUCES NO BOX AT ALL —
	BUT THE DETECTOR'S TEXT-PROBABILITY MASK STILL HAS SIGNAL THERE. ONLY TEXT PIXELS CONNECTED
	TO THE REGION (WITHIN dilate_px) JOIN THE MASK, SO NEIGHBOURING BUBBLES STAY UNTOUCHED.
	RETURNS None WHEN NOTHING ADJACENT WAS FOUND (CALLER KEEPS THE ORIGINAL POLYGON)."""
	pts = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
	seed = np.zeros(mask.shape, dtype=np.uint8)
	cv2.fillPoly(seed, [pts], 255)
	seed = cv2.dilate(seed, np.ones((dilate_px * 2 + 1, dilate_px * 2 + 1), np.uint8))

	text_bin = (mask >= thresh).astype(np.uint8) * 255
	num, labels, _stats, _centroids = cv2.connectedComponentsWithStats(text_bin, connectivity=8)

	# FIND EVERY TEXT COMPONENT THAT TOUCHES THE (DILATED) REGION — THEN INCLUDE THE *FULL*
	# COMPONENT, NOT JUST THE TOUCHING ROWS (A DOTS LINE 6PX BELOW THE LAST LINE TOUCHES ONLY
	# WITH ITS TOP ROW; CLIPPING TO THE OVERLAP WOULD LEAVE THE REST OF THE DOTS UNCOVERED).
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
	# CLAMP UPWARD AND LEFTWARD EXPANSION — TEXT ONLY GROWS RIGHTWARD (TRAILING DOTS/PUNCTUATION)
	# AND DOWNWARD (TRAILING BOTTOM DOTS LINE). NEVER INVADE A BUBBLE ABOVE OR TO THE LEFT.
	clamped_pts = [[max(int(orig_x0 - 2), int(p[0])), max(int(orig_y0 - 2), int(p[1]))] for p in hull_pts]
	return clamped_pts


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


def _region_from_box(box: np.ndarray, index: int, page_w: int, page_h: int) -> Region:
    x, y, w, h = detect.box_to_xywh(box)
    polygon = [[int(px), int(py)] for px, py in box]
    angle = detect.calculate_box_angle(box)
    return Region(
        id=f"r{index}",
        box=Box(x=x, y=y, w=w, h=h),
        polygon=polygon,
        category=detect.classify_region(box, page_w, page_h),  # type: ignore[arg-type]
        confidence=0.0,
        vertical=detect.is_vertical_box(box),
        angle=angle,
    )


def _is_multiline_comic_blob(cb: np.ndarray, rapid_boxes: list[np.ndarray]) -> bool:
	"""Check if a ComicTextDetector box is a redundant multi-line blob that spans across multiple
	vertically stacked RapidOCR lines already detected. We only drop multi-line blobs when RapidOCR
	has ALREADY detected 2 or more distinct vertical lines inside it."""
	cx, cy, cw, ch = detect.box_to_xywh(cb)
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
	if len(overlapping_rapid) < 2:
		return False  # RapidOCR did NOT detect multiple lines — preserve comic box to rescue missing lines!

	# If it overlaps with multiple vertically stacked lines, it is redundant
	for i in range(len(overlapping_rapid)):
		for j in range(i + 1, len(overlapping_rapid)):
			r1 = overlapping_rapid[i]
			r2 = overlapping_rapid[j]
			if abs(r1[1] - r2[1]) > 0.4 * min(r1[3], r2[3]):
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
			cx1, cy1 = x + w / 2, y + h / 2
			cx2, cy2 = kx + kw / 2, ky + kh / 2
			dist = np.hypot(cx1 - cx2, cy1 - cy2)
			same_text = (
				(text.strip() == k_text.strip())
				or (text.strip() and text.strip() in k_text.strip())
				or (k_text.strip() and k_text.strip() in text.strip())
			)
			if (iou >= 0.30) or (same_text and (dist < max(h, kh) * 1.5 or iou >= 0.15)):
				if len(text.strip()) > len(k_text.strip()) or (len(text.strip()) == len(k_text.strip()) and score > k_score):
					kept[k_idx] = (pts, text, max(score, k_score), line_ang)
				duplicate = True
				break
		if not duplicate:
			kept.append((pts, text, score, line_ang))
	return kept


def analyze_image(img_bgr: np.ndarray) -> AnalyzeResponse:
	page_h, page_w = img_bgr.shape[:2]

	# 0. DE-WATERMARK BUBBLES / SPEECH TEXT WITH COLLIDING CHROMATIC WATERMARKS
	ocr_img, _has_collision = watermark_remover.remove_colliding_watermarks(img_bgr)

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
		hsv = cv2.cvtColor(ocr_img, cv2.COLOR_BGR2HSV)
		sat = hsv[:, :, 1]
		val = hsv[:, :, 2]
		b, g, r = ocr_img[:, :, 0], ocr_img[:, :, 1], ocr_img[:, :, 2]
		max_c = np.maximum(np.maximum(r, g), b)
		min_c = np.minimum(np.minimum(r, g), b)
		color_diff = max_c - min_c
		color_wm = (((sat >= 30) | (color_diff >= 25)) & (val >= 40)).astype(np.uint8) * 255
		if np.count_nonzero(color_wm) > 500:
			clean_wm_img = cv2.inpaint(ocr_img, color_wm, 3, cv2.INPAINT_TELEA)
			clean_lines = ocr.recognize_full(clean_wm_img)
			for cpts, ct, cs in clean_lines:
				clean_text = re.sub(r'^[A-Za-z0-9_.\-]{1,8}\s*(?=[\u4e00-\u9fa5])', '', ct.strip())
				if detect._CHINESE_RE.search(clean_text) and not detect._is_watermark_line(clean_text):
					cx, cy, cw, ch = detect.box_to_xywh(cpts)
					replaced = False
					for idx, (rpts, rt, rs) in enumerate(rapid_lines):
						rx, ry, rw, rh = detect.box_to_xywh(rpts)
						ix = max(0, min(cx + cw, rx + rw) - max(cx, rx))
						iy = max(0, min(cy + ch, ry + rh) - max(cy, ry))
						if ix * iy >= 0.4 * min(cw * ch, rw * rh):
							has_latin = bool(re.search(r'[A-Za-z]', rt))
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
		clean_t = re.sub(r'^[A-Za-z0-9_.\-]{1,8}\s*(?=[\u4e00-\u9fa5])', '', t.strip())
		if not clean_t:
			clean_t = t
		if re.fullmatch(r'^[A-Za-z]{1,4}[.．…!！?？]{1,}$', clean_t.strip()):
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
	rapid_lines = _deduplicate_ocr_lines(normalized_rapid_lines)

	rapid_boxes = [pts for pts, _t, _s, _ang in rapid_lines]
	rapid_scores = [float(s) for _pts, _t, s, _ang in rapid_lines]
	rapid_texts = [t for _pts, t, _s, _ang in rapid_lines]

	# FILTER OUT COMIC DETECTOR BLOBS THAT SPAN MULTIPLE VERTICAL LINES.
	# RAPIDOCR PROVIDES PRECISE SINGLE-LINE DETECTIONS; INGESTING OVERLAPPING MULTI-LINE COMIC BLOBS
	# CORRUPTS LINE HEIGHTS AND SPLITS MULTI-LINE BUBBLES INTO OVERLAPPING FRAGMENTS.
	kept_comic_boxes = []
	kept_comic_scores = []
	for cb, cs in zip(comic_boxes, comic_scores):
		if not _is_multiline_comic_blob(cb, rapid_boxes):
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
			# 1. DISCARD NON-CHINESE / ENGLISH SUBTITLE LINES IF CHINESE IS PRESENT
			chinese_matched = [m for m in matched if detect._CHINESE_RE.search(m[1])]
			if chinese_matched:
				matched = chinese_matched
			# 2. DISCARD WATERMARK / SCANLATION SIGNATURE LINES IF LEGITIMATE STORY DIALOGUE IS PRESENT
			story_matched = [m for m in matched if not detect._is_watermark_line(m[1])]
			if story_matched:
				matched = story_matched

			# DEDUPLICATE IDENTICAL / NEAR-DUPLICATE MATCHED LINES INSIDE THE REGION
			unique_matched = []
			for m in matched:
				m_line, m_text, m_score, m_ang = m
				m_txt_clean = m_text.strip()
				if not m_txt_clean:
					continue
				if not any(m_txt_clean == u[1].strip() for u in unique_matched):
					unique_matched.append(m)
			matched = unique_matched

			matched.sort(key=lambda m: (m[0][:, 1].min(), m[0][:, 0].min()))
			region.text = "\n".join(t for _l, t, _s, _ang in matched if t.strip())
			region.confidence = float(max(s for _l, _t, s, _ang in matched)) if matched else 0.0

			_bx, _by, bw, bh = detect.box_to_xywh(box)
			all_pts = np.vstack([line.reshape(-1, 2) for line, _, _, _ in matched]).astype(np.float32)
			hull = cv2.convexHull(all_pts)
			if hull is not None and len(hull) >= 3:
				hull_pts = hull.reshape(-1, 2).astype(np.float64)
				_hx, _hy, hw, hh = detect.box_to_xywh(hull_pts)

				# MULTI-LINE / STAT-CARD RESCUE: IF DETECTOR BOX IS SIGNIFICANTLY TALLER THAN MATCHED LINES
				if bh >= 1.25 * hh and bh >= 45:
					crop_res = ocr.recognize_crop(ocr.crop_region(ocr_img, box))
					if crop_res and crop_res.text.strip():
						crop_lines = [cl.strip() for cl in crop_res.text.split("\n") if cl.strip()]
						current_lines = [t.strip() for _l, t, _s, _ang in matched if t.strip()]
						if any(cl not in current_lines for cl in crop_lines):
							region.text = crop_res.text.strip()
							region.confidence = max(region.confidence, crop_res.score)
							region.polygon = [[int(px), int(py)] for px, py in box]
							region.box = Box(x=_bx, y=_by, w=bw, h=bh)
							region.category = detect.classify_region(box, page_w, page_h)  # type: ignore[arg-type]
							region.vertical = detect.is_vertical_box(box)
							line_angles = [line_ang for _l, _t, _s, line_ang in matched if abs(line_ang) >= 1.2]
							if line_angles:
								med = float(np.median(line_angles))
								region.angle = 0.0 if abs(med) < 1.2 else round(med, 2)
							elif hull_pts is not None and len(hull_pts) >= 4:
								poly_ang = detect.calculate_box_angle(hull_pts)
								region.angle = 0.0 if abs(poly_ang) < 1.2 else round(poly_ang, 2)
							else:
								region.angle = detect.calculate_box_angle(box)
							regions.append(region)
							continue

				region.polygon = [[int(p[0]), int(p[1])] for p in hull_pts]
				if len(matched) == 1 and _PUNCT_TAIL.search(region.text):
					is_dots = bool(_ELLIPSIS_TAIL.search(region.text))
					punct_only = bool(_PUNCT_ONLY.fullmatch(region.text))
					widened = (
						_ellipsis_polygon(hull_pts, box, region.text, page_w)
						if is_dots
						else _punct_polygon(hull_pts, box, page_w)
					)
					if punct_only:
						widened = _pad_punct_polygon(widened, page_w)
					if widened != region.polygon:
						hull_right = float(hull_pts[:, 0].max())
						hull_h = max(1.0, float(hull_pts[:, 1].max() - hull_pts[:, 1].min()))
						widened_right = float(max(p[0] for p in widened))
						region.polygon = widened
						if is_dots:
							region.text = _append_ellipsis(region.text)
						elif not punct_only and widened_right - hull_right >= hull_h * 0.35:
							region.text = _append_punctuation(region.text)
					bx, by, bw, bh = _polygon_bounds(region.polygon)
					region.box = Box(x=bx, y=by, w=bw, h=bh)
				else:
					# REDERIVE BOX FROM THE HULL'S BOUNDING BOX
					hx, hy, hw, hh = detect.box_to_xywh(hull_pts)
					region.box = Box(x=hx, y=hy, w=max(1, hw), h=max(1, hh))
				region.category = detect.classify_region(hull_pts, page_w, page_h)  # type: ignore[arg-type]
				region.vertical = detect.is_vertical_box(hull_pts)
			# 3. COMPUTE ORIENTATION ANGLE FROM MATCHED OCR LINE ANGLES (PARAGRAPH MEDIAN)
			line_angles = [line_ang for _l, _t, _s, line_ang in matched if abs(line_ang) >= 1.2]
			if line_angles:
				med = float(np.median(line_angles))
				region.angle = 0.0 if abs(med) < 1.2 else round(med, 2)
			elif hull_pts is not None and len(hull_pts) >= 4:
				poly_ang = detect.calculate_box_angle(hull_pts)
				region.angle = 0.0 if abs(poly_ang) < 1.2 else round(poly_ang, 2)
			else:
				region.angle = detect.calculate_box_angle(box)
		else:
			ocr_result = ocr.recognize_crop(ocr.crop_region(ocr_img, box))
			if ocr_result:
				region.text = ocr_result.text
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
					region.box = Box(x=bx, y=by, w=bw, h=bh)

		# MASK-GUIDED GROWTH: TEXT PIXELS THE BOX EXTRACTION MISSED (e.g. A "......" LINE AT THE
		# BOTTOM OF A BUBBLE THAT PRODUCED NO BOX) STILL SHOW IN THE DETECTOR'S PROBABILITY MASK —
		# GROW THE INPAINT POLYGON (AND THE TYPESET BOX) TO COVER THEM, AND REFLECT A GROWN DOTS
		# LINE IN THE EXTRACTED TEXT. NEVER GROW WATERMARK OR SCANLATION REGIONS INTO STORY TEXT.
		if comic_mask is not None and region.polygon and not detect._is_watermark_line(region.text):
			prev_bottom = max(p[1] for p in region.polygon)
			prev_right = max(p[0] for p in region.polygon)
			grown = _grow_polygon_by_mask(region.polygon, comic_mask)
			if grown is not None and grown != region.polygon:
				region.polygon = grown
				bx, by, bw, bh = _polygon_bounds(grown)
				region.box = Box(x=bx, y=by, w=bw, h=bh)
				added_h = max(p[1] for p in grown) - prev_bottom
				added_w = max(p[0] for p in grown) - prev_right
				if hull_pts is not None and len(matched):
					line_h = (hull_pts[:, 1].max() - hull_pts[:, 1].min()) / len(matched)
				elif not matched:
					line_h = float(detect.box_to_xywh(box)[3])
				else:
					line_h = 0.0
				# A SHORT BAND ADDED BELOW THE TEXT IS A DOTS LINE ONLY IF THE TEXT DOES NOT END IN A TERMINATING PUNCTUATION
				if line_h > 0 and 0.35 * line_h <= added_h <= 1.05 * line_h:
					last_char = region.text.rstrip()[-1] if region.text.strip() else ""
					if last_char not in "。.;；:：!！?？)]】”’\"'":
						unit = "……" if any(ord(c) > 0x2E80 for c in region.text) else "..."
						region.text = region.text.rstrip() + "\n" + unit
				# A REAL HORIZONTAL EXTENSION TO THE RIGHT IS MISSED TRAILING PUNCTUATION OR ELLIPSIS
				# (e.g. THE "！" OF "找到军师了！！" OR THE "……" OF "这里……") — REFLECT IT IN THE EXTRACTED TEXT TOO.
				if line_h > 0 and added_w >= max(10.0, line_h * 0.25):
					if _ELLIPSIS_TAIL.search(region.text):
						region.text = _append_ellipsis(region.text)
					elif _EXCLAIM_TAIL.search(region.text) or _QUESTION_TAIL.search(region.text):
						region.text = _append_punctuation(region.text)
					elif region.text.strip() and region.text.rstrip()[-1] not in "。.;；:：!！?？)]】”’\"'":
						unit = "……" if any(ord(c) > 0x2E80 for c in region.text) else "..."
						region.text = region.text.rstrip() + unit
						# If union box extends further to the right to cover the dots, widen the mask
						poly_pts = np.asarray(region.polygon, dtype=np.float64)
						widened = _ellipsis_polygon(poly_pts, box, region.text, page_w)
						if widened != region.polygon:
							region.polygon = widened
							bx, by, bw, bh = _polygon_bounds(widened)
							region.box = Box(x=bx, y=by, w=bw, h=bh)
		regions.append(region)

	# -- STRAY-DOT CLEANUP: REC MODELS SOMETIMES SPLIT A FINAL "..." INTO THE LAST WORD PLUS A
	# SEPARATE LONE-DOT LINE (e.g. "大姐大，\n轻，轻点\n."): A TRAILING LONE-DOT LINE JOINS THE LINE ABOVE IT.
	_STRAY_DOT_LINE = re.compile(r"^[.．·…]$")
	for region in regions:
		lines = region.text.split("\n")
		if len(lines) >= 2 and _STRAY_DOT_LINE.fullmatch(lines[-1].strip()):
			region.text = "\n".join(lines[:-2] + [lines[-2].rstrip() + lines[-1].strip()])
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

	# 2) A PUNCTUATION-ONLY REGION MERGES INTO ITS NEIGHBOUR: THE LONE "." UNDER "JINGZHOU" AND
	# THE LONE "？" AFTER "穿越者！" MUST JOIN THE ADJACENT TEXT — NEVER STAND ALONE.
	final_regions: list[Region] = []
	for region in regions:
		if final_regions and _PUNCT_ONLY.fullmatch(region.text.strip()):
			prev = final_regions[-1]
			# VERTICAL: THE PUNCTUATION SITS BELOW THE TEXT (X-RANGES OVERLAP, GENEROUS GAP —
			# BUBBLES OFTEN HAVE WHITESPACE BETWEEN THE LAST WORD AND A TRAILING ".").
			v_gap = region.box.y - (prev.box.y + prev.box.h)
			x_overlap = min(region.box.x + region.box.w, prev.box.x + prev.box.w) - max(region.box.x, prev.box.x)
			vert_ok = v_gap <= prev.box.h * 6.0 and x_overlap >= min(region.box.w, prev.box.w) * 0.2
			# HORIZONTAL: THE PUNCTUATION SITS RIGHT OF THE TEXT ON THE SAME LINE (e.g. THE "？"
			# OF "穿越者！？" — OFTEN A BIT FAR FROM THE EXCLAMATION).
			h_gap = region.box.x - (prev.box.x + prev.box.w)
			y_overlap = min(region.box.y + region.box.h, prev.box.y + prev.box.h) - max(region.box.y, prev.box.y)
			horiz_ok = 0.0 <= h_gap <= prev.box.h * 2.5 and y_overlap >= region.box.h * 0.5
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
					prev.box = Box(x=bx, y=by, w=bw, h=bh)
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

	# 4) DISCARD EMPTY, DASH-ONLY NOISE, AND STANDALONE WATERMARK REGIONS (e.g. "速漫库", "qumanku.com")
	_IGNORED_NOISE_RE = re.compile(r"^[—―\-_~～\s]*$")
	final_regions = [
		r for r in final_regions
		if r.text.strip()
		and not _IGNORED_NOISE_RE.fullmatch(r.text.strip())
		and not detect.is_pure_watermark_region(r.text)
	]

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


