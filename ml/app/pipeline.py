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
	dilate_px: int = 20,
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
	return [[int(p[0]), int(p[1])] for p in hull_pts]


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
	"""Check if a ComicTextDetector box is a multi-line blob that spans across multiple vertically
	stacked RapidOCR lines. We drop multi-line blobs because RapidOCR provides precise single lines,
	while keeping single-line comic boxes (e.g. for punctuation/ellipsis extension or SFX)."""
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
	if not overlapping_rapid:
		return False  # Standalone SFX / stylized text

	# If it overlaps with multiple vertically stacked lines, it is multi-line
	if len(overlapping_rapid) >= 2:
		for i in range(len(overlapping_rapid)):
			for j in range(i + 1, len(overlapping_rapid)):
				r1 = overlapping_rapid[i]
				r2 = overlapping_rapid[j]
				if abs(r1[1] - r2[1]) > 0.4 * min(r1[3], r2[3]):
					return True

	# If its height is significantly larger than the overlapping line (e.g. 1.35x taller), it is multi-line
	avg_rh = sum(r[3] for r in overlapping_rapid) / len(overlapping_rapid)
	if ch > 1.35 * avg_rh and ch > 55:
		return True
	return False


def analyze_image(img_bgr: np.ndarray) -> AnalyzeResponse:
	page_h, page_w = img_bgr.shape[:2]

	# COMIC-TEXT-DETECTOR PRIMARY DETECTION
	comic_boxes: list[np.ndarray] = []
	comic_scores: list[float] = []
	comic_mask: np.ndarray | None = None
	backend: Literal["comic-ctd", "rapidocr-fallback"] = "rapidocr-fallback"

	if detector is not None and detector.available():
		result = detector.analyze(img_bgr)
		comic_boxes = result.boxes
		comic_scores = result.scores
		comic_mask = result.mask
		backend = result.backend

	# RAPIDOCR FULL-PAGE DET+REC — ALWAYS RUN (THE UNION'S SECOND OPINION + TEXT SOURCE)
	rapid_lines = ocr.recognize_full(img_bgr)

	# RECOVER CHINESE TEXT OBSCURED OR COVERED BY COLORED WATERMARK STAMPS (e.g. "点将:" UNDER "COLAMANHUA.com")
	try:
		hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
		mask_red1 = cv2.inRange(hsv, np.array([0, 40, 50]), np.array([15, 255, 255]))
		mask_red2 = cv2.inRange(hsv, np.array([165, 40, 50]), np.array([180, 255, 255]))
		mask_blue = cv2.inRange(hsv, np.array([85, 40, 50]), np.array([130, 255, 255]))
		color_wm = mask_red1 | mask_red2 | mask_blue
		if np.count_nonzero(color_wm) > 500:
			clean_wm_img = cv2.inpaint(img_bgr, color_wm, 3, cv2.INPAINT_TELEA)
			clean_lines = ocr.recognize_full(clean_wm_img)
			for pts, t, s in clean_lines:
				if detect._CHINESE_RE.search(t) and not detect._is_watermark_line(t):
					x, y, w, h = detect.box_to_xywh(pts)
					covered = False
					for rpts, rt, _rs in rapid_lines:
						if detect._CHINESE_RE.search(rt) and not detect._is_watermark_line(rt):
							rx, ry, rw, rh = detect.box_to_xywh(rpts)
							ix = max(0, min(x + w, rx + rw) - max(x, rx))
							iy = max(0, min(y + h, ry + rh) - max(y, ry))
							if ix * iy >= 0.5 * w * h:
								covered = True
								break
					if not covered:
						rapid_lines.append((pts, t, s))
	except Exception:
		pass

	# CLEAN STRAY LATIN NOISE BEFORE ELLIPSIS / PUNCTUATION (e.g. "NMT..." -> "……")
	normalized_rapid_lines = []
	for pts, t, s in rapid_lines:
		line_angle = detect.calculate_box_angle(pts)
		clean_t = t
		if re.fullmatch(r'^[A-Za-z]{1,4}[.．…!！?？]{1,}$', t.strip()):
			has_bang = "!" in t or "！" in t
			has_q = "?" in t or "？" in t
			if has_bang and has_q:
				clean_t = "……！？"
			elif has_bang:
				clean_t = "……！"
			elif has_q:
				clean_t = "……？"
			else:
				clean_t = "……"
		normalized_rapid_lines.append((pts, clean_t, s, line_angle))
	rapid_lines = normalized_rapid_lines

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
			matched.sort(key=lambda m: (m[0][:, 1].min(), m[0][:, 0].min()))
			region.text = "\n".join(t for _l, t, _s, _ang in matched if t.strip())
			region.confidence = float(max(s for _l, _t, s, _ang in matched)) if matched else 0.0
			# USE THE CONVEX HULL OF ALL MATCHED RAPID-LINE CORNER POINTS AS:
			#   1) THE INPAINT POLYGON — TIGHTER THAN THE GROUPED AABB UNION.
			#   2) THE BASIS FOR region.box AND region.category — THE RAPIDOCR LINE POLYGONS
			#      ARE TIGHT AROUND ACTUAL TEXT, SO THEIR HULL'S BOUNDING BOX IS THE REAL TEXT
			#      EXTENT. THE ORIGINAL GROUPED BOX IS THE AABB UNION (OFTEN MUCH WIDER), WHICH
			#      CAUSES classify_region TO MISFIRE AND THE TYPESETTER TO PICK ENORMOUS FONTS.
			all_pts = np.vstack([line.reshape(-1, 2) for line, _, _, _ in matched]).astype(np.float32)
			hull = cv2.convexHull(all_pts)
			if hull is not None and len(hull) >= 3:
				hull_pts = hull.reshape(-1, 2).astype(np.float64)
				region.polygon = [[int(p[0]), int(p[1])] for p in hull_pts]
				# TRAILING-PUNCTUATION RECOVERY — A SINGLE-LINE REGION WHOSE TEXT ENDS IN DOTS OR
				# !/? MAY BE TRUNCATED ("......" READ AS "...", "穿越者！？" READ AS "穿越者！"):
				# WIDEN THE ERASE MASK SO THE MISSING GLYPHS GET INPAINTED TOO.
				if len(matched) == 1 and _PUNCT_TAIL.search(region.text):
					is_dots = bool(_ELLIPSIS_TAIL.search(region.text))
					punct_only = bool(_PUNCT_ONLY.fullmatch(region.text))
					widened = (
						_ellipsis_polygon(hull_pts, box, region.text, page_w)
						if is_dots
						else _punct_polygon(hull_pts, box, page_w)
					)
					if punct_only:
						# A LONE GLYPH: PAD THE MASK (DETECTOR BOXES OFTEN CLIP THE GLYPH) —
						# NEVER FABRICATE TEXT FOR IT.
						widened = _pad_punct_polygon(widened, page_w)
					if widened != region.polygon:
						hull_right = float(hull_pts[:, 0].max())
						hull_h = max(1.0, float(hull_pts[:, 1].max() - hull_pts[:, 1].min()))
						widened_right = float(max(p[0] for p in widened))
						region.polygon = widened
						# THE EXTRACTED TEXT MUST SHOW THE GLYPHS THE MASK NOW COVERS. PUNCTUATION
						# APPENDS ONLY WHEN THE GEOMETRY PROVED REAL EXTRA SPACE (≥0.35 LINE
						# HEIGHT) — BOX PADDING ALONE MUST NOT FABRICATE A "？".
						if is_dots:
							region.text = _append_ellipsis(region.text)
						elif not punct_only and widened_right - hull_right >= hull_h * 0.35:
							region.text = _append_punctuation(region.text)
					# THE TYPESET BOX FOLLOWS THE (WIDENED) MASK — OTHERWISE THE RENDERED
					# TRANSLATION SITS OFF-CENTER RELATIVE TO THE FULL LINE.
					bx, by, bw, bh = _polygon_bounds(region.polygon)
					region.box = Box(x=bx, y=by, w=bw, h=bh)
				else:
					# REDERIVE BOX FROM THE HULL'S BOUNDING BOX (TIGHT — d12f433 BEHAVIOUR)
					hx, hy, hw, hh = detect.box_to_xywh(hull_pts)
					region.box = Box(x=hx, y=hy, w=max(1, hw), h=max(1, hh))
				region.category = detect.classify_region(hull_pts, page_w, page_h)  # type: ignore[arg-type]
				region.vertical = detect.is_vertical_box(hull_pts)
			# 3. COMPUTE ORIENTATION ANGLE FROM MATCHED OCR LINE ANGLES (PARAGRAPH MEDIAN)
			line_angles = [line_ang for _l, _t, _s, line_ang in matched]
			if line_angles:
				med = float(np.median(line_angles))
				region.angle = 0.0 if abs(med) < 3.0 else round(med, 2)
		else:
			ocr_result = ocr.recognize_crop(ocr.crop_region(img_bgr, box))
			if ocr_result:
				region.text = ocr_result.text
				region.confidence = ocr_result.score
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
				# A REAL HORIZONTAL EXTENSION TO THE RIGHT IS MISSED TRAILING PUNCTUATION
				# (e.g. THE "！" OF "找到军师了！！") — REFLECT IT IN THE EXTRACTED TEXT TOO.
				if line_h > 0 and added_w >= line_h * 0.35:
					if _ELLIPSIS_TAIL.search(region.text):
						region.text = _append_ellipsis(region.text)
					else:
						region.text = _append_punctuation(region.text)
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

	# 4) DISCARD EMPTY OR DASH-ONLY FALSE DETECTIONS (e.g. "—", speed lines, drawing strokes)
	_IGNORED_NOISE_RE = re.compile(r"^[—―\-_~～\s]*$")
	final_regions = [
		r for r in final_regions
		if r.text.strip() and not _IGNORED_NOISE_RE.fullmatch(r.text.strip())
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


