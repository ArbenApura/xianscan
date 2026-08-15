# COMIC TEXT DETECTOR — ONNX RUNTIME PORT OF manga-image-translator's ctd.py (Apache-2.0).
#
# THE `comictextdetector.pt.onnx` MODEL (FROM THE beta-0.3 RELEASE) OUTPUTS THREE TENSORS:
#   [0] YOLO BLOB      — UNUSED (UPSTREAM ONLY USES IT FOR LANGUAGE CLASSIFICATION WE DON'T NEED)
#   [1] MASK           — TEXT PROBABILITY MAP (1×1024×1024 — FULL INPUT RESOLUTION, EMPIRICALLY)
#   [2] LINES_MAP      — DB TEXT-LINE PROBABILITY MAP (1×2×1024×1024; CHANNEL 0 IS THE DB MAP)
#
# PREPROCESSING (PINNED TO ctd.py/preprocess_img + imgproc_utils.letterbox):
#   BGR→RGB, LETTERBOX-RESIZE TO 1024×1024 (BOTTOM-RIGHT BLACK PAD), TRANSPOSE TO CHW, REVERSE
#   CHANNELS (THE EXPORT EXPECTS BGR ORDER), /255.
# POSTPROCESSING (PINNED TO ctd.py/_infer):
#   CROP THE PADDING OFF BOTH MAPS, DB boxes_from_bitmap (thresh 0.3, box_thresh 0.6, unclip 1.5),
#   SCALE TO ORIGINAL DIMENSIONS, RESIZE MASK BACK TO ORIGINAL SIZE.
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import config

# -- PURE HELPERS (UNIT-TESTED) -- #


def letterbox(
	im: np.ndarray,
	new_shape: tuple[int, int] = (1024, 1024),
	color: tuple[int, int, int] = (0, 0, 0),
	auto: bool = False,
	stride: int = 64,
) -> tuple[np.ndarray, tuple[float, float], tuple[int, int]]:
	"""RESIZE KEEPING ASPECT + PAD TO new_shape (BOTTOM-RIGHT). PORT OF imgproc_utils.letterbox.

	RETURNS (padded_image, (ratio_x, ratio_y), (pad_w, pad_h)).
	"""
	shape = im.shape[:2]
	r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
	ratio = (r, r)
	new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
	dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
	if auto:
		dw, dh = np.mod(dw, stride), np.mod(dh, stride)
	dh, dw = int(dh), int(dw)

	if shape[::-1] != new_unpad:
		im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
	im = cv2.copyMakeBorder(im, 0, dh, 0, dw, cv2.BORDER_CONSTANT, value=color)
	return im, ratio, (dw, dh)


def preprocess_for_onnx(img_bgr: np.ndarray, input_size: int = 1024) -> tuple[np.ndarray, tuple[int, int]]:
	"""BGR IMAGE → (1, 3, S, S) FLOAT32 TENSOR (BGR CHANNEL ORDER, /255) + (pad_w, pad_h)."""
	img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
	img_in, _ratio, (dw, dh) = letterbox(img_rgb, new_shape=(input_size, input_size), auto=False, stride=64)
	# HWC → CHW, THEN REVERSE CHANNELS (RGB→BGR — THE ONNX EXPORT'S TRAINED ORDER), /255
	tensor = img_in.transpose((2, 0, 1))[::-1][None].astype(np.float32) / 255.0
	return tensor, (dw, dh)


def crop_padded(map2d: np.ndarray, pad_w: int, pad_h: int) -> np.ndarray:
	"""STRIP THE LETTERBOX PADDING FROM A (H, W) PROBABILITY MAP (PAD SITS AT BOTTOM-RIGHT)."""
	h, w = map2d.shape[:2]
	return map2d[: h - pad_h, : w - pad_w]


def box_score_fast(bitmap: np.ndarray, contour: np.ndarray) -> float:
	"""MEAN PROBABILITY INSIDE A CONTOUR — PORT OF db_utils.box_score_fast (numpy-only)."""
	h, w = bitmap.shape[:2]
	box = contour.copy()
	xmin = int(np.clip(np.floor(box[:, 0].min()), 0, w - 1))
	xmax = int(np.clip(np.ceil(box[:, 0].max()), 0, w - 1))
	ymin = int(np.clip(np.floor(box[:, 1].min()), 0, h - 1))
	ymax = int(np.clip(np.ceil(box[:, 1].max()), 0, h - 1))
	mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
	shifted = box.copy()
	shifted[:, 0] -= xmin
	shifted[:, 1] -= ymin
	cv2.fillPoly(mask, shifted.reshape(1, -1, 2).astype(np.int32), 1)
	return float(cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0])


def unclip_polygon(box: np.ndarray, unclip_ratio: float = 1.5) -> np.ndarray | None:
	"""EXPAND A BOX BY area*ratio/perimeter — PORT OF db_utils.unclip (pyclipper)."""
	import pyclipper

	# OPENCV 5 REQUIRES CV_32F/CV_32S FOR contourArea/arcLength — UPSTREAM'S TORCH TENSORS WERE CV_64F
	box32 = box.astype(np.float32)
	area = cv2.contourArea(box32)
	perimeter = cv2.arcLength(box32, True)
	if perimeter <= 0:
		return None
	distance = area * unclip_ratio / perimeter
	offset = pyclipper.PyclipperOffset()
	offset.AddPath(box.tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
	expanded = offset.Execute(distance)
	if not expanded:
		return None
	return np.array(expanded[0], dtype=np.float64).reshape(-1, 2)


def get_mini_boxes(contour: np.ndarray) -> tuple[list[list[float]], float]:
	"""MIN-AREA RECTANGLE WITH THE STANDARD 4-POINT ORDERING — PORT OF db_utils.get_mini_boxes."""
	bounding_box = cv2.minAreaRect(contour.astype(np.float32))
	points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])
	index_1, index_2, index_3, index_4 = 0, 1, 2, 3
	if points[1][1] > points[0][1]:
		index_1, index_4 = 0, 1
	else:
		index_1, index_4 = 1, 0
	if points[3][1] > points[2][1]:
		index_2, index_3 = 2, 3
	else:
		index_2, index_3 = 3, 2
	box = [points[index_1], points[index_2], points[index_3], points[index_4]]
	return box, float(min(bounding_box[1]))


def lines_map_to_boxes(
	lines_map: np.ndarray,
	dest_width: int,
	dest_height: int,
	thresh: float = 0.3,
	box_thresh: float = 0.6,
	unclip_ratio: float = 1.5,
	max_candidates: int = 1000,
	min_side: int = 5,
) -> tuple[list[np.ndarray], list[float]]:
	"""DB REPRESENTER (boxes_from_bitmap PORT) — (H, W) PROBABILITY MAP → (boxes, scores).

	EACH BOX IS A (4, 2) INT ARRAY IN *ORIGINAL IMAGE* COORDINATES. PURE — UNIT-TESTED.
	"""
	if lines_map.ndim == 4:
		lines_map = lines_map[0, 0]
	bitmap = lines_map > thresh
	height, width = bitmap.shape
	contours, _ = cv2.findContours((bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

	boxes: list[np.ndarray] = []
	scores: list[float] = []
	for contour in contours[:max_candidates]:
		contour = contour.squeeze(1)
		if contour.shape[0] < 4:
			continue
		score = box_score_fast(lines_map, contour)
		if box_thresh > score:
			continue
		points, sside = get_mini_boxes(contour)
		if sside < 2:
			continue
		points = np.array(points)
		expanded = unclip_polygon(points, unclip_ratio)
		if expanded is None:
			continue
		box, sside = get_mini_boxes(expanded)
		if sside < min_side:
			continue
		box = np.array(box)
		box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
		box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
		boxes.append(box.astype(np.int64))
		scores.append(score)
	return boxes, scores


def box_to_xywh(box: np.ndarray) -> tuple[int, int, int, int]:
	"""(4, 2) BOX → (x, y, w, h) AXIS-ALIGNED."""
	xs = box[:, 0]
	ys = box[:, 1]
	return int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min())


def is_vertical_box(box: np.ndarray) -> bool:
	"""HEURISTIC: A REGION TALLER THAN WIDE (×1.2) IS LIKELY VERTICAL CJK TEXT."""
	_x, _y, w, h = box_to_xywh(box)
	return h > w * 1.2


def calculate_box_angle(box: np.ndarray | list[list[int | float]]) -> float:
	"""CALCULATE ORIENTATION ANGLE IN DEGREES [-45, 45] OF A 4-POINT POLYGON / BOX OR CONTOUR.
	ANGLES WITH MAGNITUDE < 1.5 DEGREES ARE ROUNDED TO 0.0 TO PREVENT SUBPIXEL BLUR ON HORIZONTAL TEXT.
	VERTICAL TEXT COLUMNS OR STEEP BOXES RETURN 0.0 BECAUSE ENGLISH TRANSLATIONS IN VERTICAL SPEECH
	BUBBLES ARE ALWAYS TYPESET HORIZONTALLY (UPRIGHT), NEVER ROTATED 90° SIDEWAYS.
	"""
	pts = np.array(box, dtype=np.float32).reshape(-1, 2)
	if len(pts) < 3:
		return 0.0

	if len(pts) == 4:
		# Standardize point ordering to [TL, TR, BR, BL]
		sorted_x = sorted(list(pts), key=lambda p: p[0])
		tl, bl = (sorted_x[0], sorted_x[1]) if sorted_x[0][1] < sorted_x[1][1] else (sorted_x[1], sorted_x[0])
		tr, br = (sorted_x[2], sorted_x[3]) if sorted_x[2][1] < sorted_x[3][1] else (sorted_x[3], sorted_x[2])

		dx = float(tr[0] - tl[0] + br[0] - bl[0]) / 2.0
		dy = float(tr[1] - tl[1] + br[1] - bl[1]) / 2.0
		if dx == 0 and dy == 0:
			return 0.0
		angle_rad = np.arctan2(dy, dx)
		angle_deg = float(np.degrees(angle_rad))
	else:
		rect = cv2.minAreaRect(pts)
		(cx, cy), (rw, rh), rect_angle = rect
		if rw < rh:
			rect_angle += 90.0
		angle_deg = float(rect_angle)

	while angle_deg > 90.0:
		angle_deg -= 180.0
	while angle_deg < -90.0:
		angle_deg += 180.0

	# Short square-like boxes (w / h <= 1.6) have noisy baseline fits; snap angles < 5.0° to 0.0
	if len(pts) == 4:
		box_w = float(tr[0] - tl[0] + br[0] - bl[0]) / 2.0
		box_h = float(bl[1] - tl[1] + br[1] - tr[1]) / 2.0
		if box_w <= 1.6 * max(1.0, box_h) and abs(angle_deg) < 5.0:
			return 0.0

	# Slant angles > 45 degrees are vertical box aspect ratio artifacts, not text baseline slants.
	# English typeset text is always horizontal; only moderate slants [-45°, 45°] (e.g. dynamic SFX) are rotated.
	if abs(angle_deg) < 1.5 or abs(angle_deg) > 45.0:
		return 0.0

	return round(angle_deg, 2)




def box_iou(a: np.ndarray, b: np.ndarray) -> float:
	"""INTERSECTION-OVER-UNION OF TWO AXIS-ALIGNED BOXES (PURE — UNIT-TESTED)."""
	ax0, ay0, aw, ah = box_to_xywh(a)
	bx0, by0, bw, bh = box_to_xywh(b)
	ax1, ay1, bx1, by1 = ax0 + aw, ay0 + ah, bx0 + bw, by0 + bh
	ix = max(0, min(ax1, bx1) - max(ax0, bx0))
	iy = max(0, min(ay1, by1) - max(ay0, by0))
	inter = ix * iy
	union = aw * ah + bw * bh - inter
	return inter / union if union > 0 else 0.0


def line_center_inside(line: np.ndarray, region: np.ndarray) -> bool:
	"""IS THE LINE'S CENTER INSIDE THE REGION BOX? (PURE — THE PARAGRAPH-MATCHING RULE.)"""
	lx0, ly0, lw, lh = box_to_xywh(line)
	rx0, ry0, rw, rh = box_to_xywh(region)
	cx = lx0 + lw / 2
	cy = ly0 + lh / 2
	return rx0 <= cx <= rx0 + rw and ry0 <= cy <= ry0 + rh


_URL_RE = re.compile(r'(\.com|\.net|\.org|\.cn|\.cc|\.xyz|\.top|http)', re.IGNORECASE)
_CHINESE_RE = re.compile(r'[\u4e00-\u9fa5\u3400-\u4dbf\U00020000-\U0002A6DF\u3000-\u303f\uff00-\uffef\u2026]')
_WATERMARK_RE = re.compile(
	r'(\.com|\.net|\.org|\.cn|\.cc|\.xyz|\.top|\.me|\.tv|\.app|http|'
	r'速漫库|速漫|漫库|qumanku|quman|包子|baozimh|baozi|colamanga|colamanhua|colam|'
	r'acloudmerge|acloud|loudmer|udmer|merd|oamanhua|'
	r'merge|cloud|manga|manhua|comic|'
	r'yumanhua|mangabox|comick|腾讯|微信|公众号|qq群|企鹅群|群号|'
	r'严禁转载|独家|扫图|录入|修图|嵌字|翻译|汉化组|'
	r'免费漫画|最新免费|漫画网|看漫画|首发|独家首发|漫客[栈拌]|漫客|mkzhan|nga\.com)',
	re.IGNORECASE,
)


def _is_watermark_line(text: str | None) -> bool:
	if not text:
		return False
	trimmed = text.strip()
	if not trimmed:
		return False
	if _WATERMARK_RE.search(trimmed):
		return True
	return False


def is_pure_watermark_region(text: str | None) -> bool:
	"""CHECK IF A REGION IS EXCLUSIVELY A WATERMARK / SCANLATION SIGNATURE (NO REAL DIALOGUE)."""
	if not text:
		return False
	trimmed = text.strip()
	if not trimmed:
		return False
	if _WATERMARK_RE.search(trimmed):
		cleaned = _WATERMARK_RE.sub('', trimmed)
		cleaned = _WATERMARK_RE.sub('', cleaned)
		cleaned = re.sub(r'[\s0-9a-zA-Z_.\-:：/\\!！?？.。…·~～()（）\[\]【】]', '', cleaned)
		if len(cleaned) <= 1:
			return True
	if not bool(_CHINESE_RE.search(trimmed)):
		if bool(_URL_RE.search(trimmed)) or bool(_WATERMARK_RE.search(trimmed)):
			return True
	return False



_TERMINAL_PUNCTUATION = tuple("。!！?？…）】”\"'~～:：;；")


def merge_text_lines(
	boxes: list[np.ndarray],
	scores: list[float],
	texts: list[str] | None = None,
	overlap_min: float = 0.40,
	gap_factor: float = 0.55,
	height_sim_max: float = 1.35,
) -> tuple[list[np.ndarray], list[float]]:
	"""MERGE HORIZONTAL TEXT BOXES THAT SIT ON THE SAME LINE (PORT OF manga-image-translator's
	textline_merge CONCEPT — THE DB REPRESENTER SPLITS ONE LINE WHEREVER THE LINE MAP DIPS).

	RULE (PURE — UNIT-TESTED):
	  - SAME LINE: VERTICAL OVERLAP ≥ overlap_min × min(heights)
	  - SAME FONT SIZE: HEIGHTS MATCH (max/min ≤ height_sim_max)
	  - MERGE WHEN: HORIZONTAL GAP ≤ gap_factor × max(heights) — A NEGATIVE GAP (OVERLAP) ALWAYS MERGES
	  - WATERMARK ISOLATION: A WATERMARK STAMP AT THE CORNER (e.g. "速漫库") MUST NEVER MERGE
	    WITH A STORY DIALOGUE LINE ON THE SAME ROW.
	  - TERMINAL PUNCTUATION GUARD: IF THE PRECEDING TEXT ALREADY ENDS IN TERMINAL PUNCTUATION
	    ('！', '。', '？', etc.) AND THERE IS A NON-NEGATIVE GAP, DO NOT MERGE ACROSS SPEECH BUBBLES.
	  - VERTICAL TEXT COLUMNS (h > 1.2×w) ARE NEVER MERGED — SIDE-BY-SIDE COLUMNS LOOK IDENTICAL
	    TO SAME-LINE BOXES BY THIS RULE AND MUST STAY SEPARATE REGIONS
	RETURNS (merged_boxes, merged_scores) — EACH MERGED BOX IS THE UNION (AXIS-ALIGNED), SCORE = MAX.
	"""
	if not boxes:
		return [], []
	if texts is None:
		texts = [''] * len(boxes)
	items = sorted(zip(boxes, scores, texts), key=lambda p: p[0][:, 0].min())
	lines: list[list] = []  # [x0, y0, x1, y1, score, is_wm, text]
	for box, score, txt in items:
		x, y, w, h = box_to_xywh(box)
		x1, y1 = x + w, y + h
		is_wm = _is_watermark_line(txt)
		if h > w * 1.2:
			# VERTICAL COLUMN — ITS OWN LINE, NEVER HORIZONTALLY MERGED
			lines.append([x, y, x1, y1, score, is_wm, txt])
			continue
		placed = False
		cy = y + h / 2.0
		for ln in lines:
			lx0, ly0, lx1, ly1, lscore, l_is_wm, l_txt = ln
			# NEVER MERGE A WATERMARK STAMP INTO STORY DIALOGUE
			if is_wm != l_is_wm:
				continue
			lh = ly1 - ly0
			min_h = min(h, lh)
			overlap = min(y1, ly1) - max(y, ly0)
			lcy = ly0 + lh / 2.0

			# MUST BE ON THE SAME HORIZONTAL LINE (y-centers aligned within 40% of line height)
			if abs(cy - lcy) > 0.40 * min_h or overlap < overlap_min * min_h:
				continue

			gap = x - lx1
			if gap > gap_factor * max(h, lh):
				continue

			# CO-LOCATED DETECTION (TWO DETECTORS / UNION ON SAME LINE):
			x_inter = min(x1, lx1) - max(x, lx0)
			min_w = min(w, lx1 - lx0)
			is_same_line_detection = (x_inter >= 0.40 * min_w) and (overlap >= 0.40 * min_h)

			# TRAILING PUNCTUATION / ELLIPSIS / DASH SEGMENT:
			# A short trailing segment (dots ……, dashes ——, punctuation) sits within the line's Y-band and immediately right of the line
			has_words = bool(txt.strip() and _CHINESE_RE.search(txt))
			is_trailing_segment = (
				(overlap >= 0.70 * min_h)
				and (x >= lx0)
				and (gap <= gap_factor * max(h, lh))
				and (gap >= -0.50 * max(h, lh))
				and not has_words
				and (h <= 0.65 * lh or w <= 160 or not txt.strip() or bool(_PUNCT_ONLY.fullmatch(txt.strip()) or _ALL_ELLIPSIS.fullmatch(txt.strip())))
			)

			if not is_same_line_detection and not is_trailing_segment and max(h, lh) / max(1.0, float(min_h)) > height_sim_max:
				continue
			# SUSPICIOUS X-OVERLAP GUARD: WHEN TWO BOXES OVERLAP IN X BY MORE THAN
			# 0.30 x max_line_height (gap << 0) AND THE RESULTING UNION IS SIGNIFICANTLY
			# WIDER THAN EITHER BOX ALONE (NOT A NEAR-DUPLICATE), THEY ALMOST CERTAINLY
			# BELONG TO DIFFERENT SPEECH BUBBLES SITTING AT THE SAME HEIGHT IN THE PANEL.
			#
			# PAGE-678: LEFT BUBBLE'S LAST LINE x=[82,210] AND RIGHT BUBBLE'S FIRST LINE
			# x=[184,384] HAVE gap=-26, union_w=302 vs max_w=200 (1.51x) -> REJECT.
			# A NEAR-DUPLICATE (TWO DETECTORS, SAME LINE) HAS union_w <= max_w*1.20 -> ALLOW.
			if gap < -max(h, lh) * 0.30 and not is_trailing_segment:
				union_w = max(x1, lx1) - min(x, lx0)
				if union_w > max(w, lx1 - lx0) * 1.20:
					continue  # DIFFERENT SPEECH BUBBLES -- DO NOT HORIZONTALLY MERGE

			# TERMINAL PUNCTUATION GUARD: IF THE PRECEDING BOX ENDS IN TERMINAL PUNCTUATION
			# AND THERE IS A NON-NEGATIVE GAP, IT IS A FINISHED SENTENCE IN ANOTHER BUBBLE.
			if l_txt and l_txt.rstrip().endswith(_TERMINAL_PUNCTUATION) and gap >= 0:
				continue

			ln[0] = min(lx0, x)
			ln[1] = min(ly0, y)
			ln[2] = max(lx1, x1)
			ln[3] = max(ly1, y1)
			ln[4] = max(lscore, score)
			ln[6] = (l_txt + ' ' + txt).strip()
			placed = True
			break
		if not placed:
			lines.append([x, y, x1, y1, score, is_wm, txt])
	merged_boxes = [
		np.array([[l[0], l[1]], [l[2], l[1]], [l[2], l[3]], [l[0], l[3]]], dtype=np.float64) for l in lines
	]
	merged_scores = [l[4] for l in lines]
	return merged_boxes, merged_scores


def _is_url_or_non_chinese(text: str | None) -> bool:
	"""Check if text is a watermark, URL, or contains zero Chinese characters (e.g. scanlation watermarks)."""
	return _is_watermark_line(text)


def group_paragraphs(
	boxes: list[np.ndarray],
	scores: list[float],
	texts: list[str] | None = None,
	overlap_min: float = 0.20,
	gap_factor: float = 0.45,
	height_sim_max: float = 1.50,
	centroid_drift_max: float = 0.60,
) -> tuple[list[np.ndarray], list[float]]:
	"""GROUP VERTICALLY STACKED TEXT LINES INTO PARAGRAPHS (A MULTI-LINE SPEECH BUBBLE).

	THE DETECTORS EMIT ONE BOX PER LINE; A 3-LINE BUBBLE BECOMES 3 REGIONS → 3 SEPARATE
	TRANSLATIONS + 3 SCATTERED TYPESET LINES (THE "TRASH" OUTPUT). THIS JOINS LINES THAT
	BELONG TO THE SAME BUBBLE/PARAGRAPH:
	  - SAME PARAGRAPH: THE NEXT LINE'S X-RANGE OVERLAPS THE PREVIOUS LINE'S BY ≥ overlap_min
	    × min(widths) (BUBBLE LINES ARE ROUGHLY CENTER-ALIGNED), AND
	  - THE VERTICAL GAP BETWEEN THEM IS ≤ gap_factor × min(heights) (BUBBLE LEADING IS TIGHT;
	    THE GAP BETWEEN SEPARATE BUBBLES IS LARGER), AND
	  - SAME FONT SIZE: LINE HEIGHTS MATCH (max/min ≤ height_sim_max). TWO BUBBLES STACKED
	    VERTICALLY WITH DIFFERENT FONT SIZES (e.g. A SMALL DIALOGUE BUBBLE ABOVE A LARGE NARRATION)
	    MUST STAY SEPARATE REGIONS, AND
	  - WATERMARK / URL SEPARATION: ENGLISH SCANLATION URLS (.com, .net) OR NON-CHINESE STAMPS
	    NEVER GROUP INTO CHINESE DIALOGUE BUBBLES.
	VERTICAL TEXT COLUMNS (h > 2.2×w) NEVER GROUP — EACH COLUMN IS ITS OWN PARAGRAPH.
	RETURNS (paragraph_boxes, scores) — THE UNION BOX PER PARAGRAPH; STANDALONE LINES UNCHANGED.
	"""
	if not boxes:
		return [], []
	if texts is None:
		texts = [''] * len(boxes)
	# STANDALONE VERTICAL STRIPES (h > 2.2×w) ARE THEIR OWN PARAGRAPHS (NEVER GROUPED); HORIZONTAL LINES GROUP BY GEOMETRY
	paragraphs: list[list[np.ndarray]] = []
	para_scores: list[float] = []
	para_is_url: list[bool] = []
	# X-CENTROID DRIFT GUARD: TRACK THE RUNNING MEAN X-CENTER OF EACH PARAGRAPH'S LINES.
	# WHEN A CANDIDATE LINE'S X-CENTER DEVIATES BY MORE THAN centroid_drift_max × max(w, lw)
	# FROM THE PARAGRAPH'S ESTABLISHED CENTER, IT BELONGS TO A DIFFERENT BUBBLE.
	para_cx_lists: list[list[float]] = []
	para_texts: list[list[str]] = []

	for box, score, txt in zip(boxes, scores, texts):
		x, y, w, h = box_to_xywh(box)
		if h > w * 2.2:
			paragraphs.append([box])
			para_scores.append(score)
			para_is_url.append(_is_url_or_non_chinese(txt))
			para_cx_lists.append([x + w / 2.0])
			para_texts.append([txt])

	horizontal = sorted(
		((b, s, t) for b, s, t in zip(boxes, scores, texts) if box_to_xywh(b)[3] <= box_to_xywh(b)[2] * 2.2),
		key=lambda p: (box_to_xywh(p[0])[1], box_to_xywh(p[0])[0]),
	)

	for box, score, txt in horizontal:
		x, y, w, h = box_to_xywh(box)
		x1 = x + w
		box_url = _is_url_or_non_chinese(txt)
		placed = False

		for p_idx, (para, _ps) in enumerate(zip(paragraphs, para_scores)):
			# PREVENT MERGING SCANLATION WATERMARK URLS INTO CHINESE DIALOGUE
			if box_url != para_is_url[p_idx]:
				continue

			last = para[-1]
			lx, ly, lw, lh = box_to_xywh(last)
			lx1 = lx + lw

			# VERTICAL CONTIGUITY: THE NEW LINE SITS AT OR BELOW THE PARAGRAPH'S BOTTOM LINE.
			gap = y - (ly + lh)
			is_parenthetical = bool(re.match(r"^[（\(\[【〔*]", txt.strip())) or bool(re.search(r"[）\)\]】〕]$", txt.strip()))
			is_trailing_tail = (
				(w <= max(80, int(lw * 0.65)) and h <= lh * 1.75)
				or (len(txt.strip()) > 0 and len(txt.strip()) <= 4 and h <= lh * 1.80)
				or is_parenthetical
			)
			gap_multiplier = 2.8 if is_parenthetical else (1.4 if is_trailing_tail else 1.0)
			max_allowed_gap = gap_factor * gap_multiplier * min(h, lh)
			if gap > max_allowed_gap or y < ly - 0.35 * min(h, lh):
				continue

			# HORIZONTAL CENTROID
			new_cx = x + w / 2.0
			para_mean_cx = sum(para_cx_lists[p_idx]) / len(para_cx_lists[p_idx])

			# Terminal punctuation guard:
			# 1. Full-stops '。' and semicolons ';；' signify complete statements.
			# 2. Exclamation '！' and question marks '？' on short interjections/utterances (<= 5 chars)
			#    or with large vertical gap / horizontal offset signify separate speech bubbles.
			last_txt = para_texts[p_idx][-1] if para_texts[p_idx] else ""
			if last_txt:
				last_strip = last_txt.strip()
				if bool(re.search(r"[。;；]$", last_strip)) and (gap >= 0.15 * min(h, lh) or abs(new_cx - para_mean_cx) > 0.35 * min(w, lw)):
					continue
				if bool(re.search(r"[!！?？]$", last_strip)):
					is_short_utterance = len(last_strip) <= 5
					has_noticeable_gap = gap >= 0.30 * min(h, lh)
					has_offset = abs(new_cx - para_mean_cx) > 0.45 * min(w, lw)
					if (is_short_utterance and gap >= 0.20 * min(h, lh)) or has_noticeable_gap or has_offset:
						continue

			# FONT-SIZE GATE: ONLY LINES OF SIMILAR FONT SIZE GROUP (OR SHORT TRAILING LINE / ELLIPSIS / PARENTHETICAL).
			height_ratio = max(h, lh) / max(1.0, float(min(h, lh)))
			if is_trailing_tail or is_parenthetical:
				if height_ratio > 2.5:
					continue
			elif height_ratio > height_sim_max:
				continue

			# HORIZONTAL ALIGNMENT: X-RANGES OVERLAP LIKE CENTERED BUBBLE LINES
			overlap = min(x1, lx1) - max(x, lx)
			if overlap < overlap_min * min(w, lw):
				continue

			# X-CENTROID DRIFT GUARD: REJECT IF THE NEW LINE'S X-CENTER DEVIATES TOO FAR
			# FROM THE PARAGRAPH'S ESTABLISHED MEAN X-CENTER.
			# If lines share a common left or right margin (e.g. stat card / system box / narrative block), allow max(w, lw).
			is_left_aligned = abs(x - lx) <= 0.25 * min(w, lw)
			is_right_aligned = abs(x1 - lx1) <= 0.25 * min(w, lw)
			if is_trailing_tail or is_parenthetical or is_left_aligned or is_right_aligned:
				if abs(new_cx - para_mean_cx) > centroid_drift_max * max(w, lw):
					continue
			elif abs(new_cx - para_mean_cx) > centroid_drift_max * min(w, lw):
				continue

			para.append(box)
			para_cx_lists[p_idx].append(new_cx)
			para_texts[p_idx].append(txt)
			para_scores[p_idx] = max(para_scores[p_idx], score)
			placed = True
			break

		if not placed:
			paragraphs.append([box])
			para_scores.append(score)
			para_is_url.append(box_url)
			para_cx_lists.append([x + w / 2.0])
			para_texts.append([txt])

	merged = []
	mscores = []
	for para, ps in zip(paragraphs, para_scores):
		x0 = min(box_to_xywh(b)[0] for b in para)
		y0 = min(box_to_xywh(b)[1] for b in para)
		x1 = max(box_to_xywh(b)[0] + box_to_xywh(b)[2] for b in para)
		y1 = max(box_to_xywh(b)[1] + box_to_xywh(b)[3] for b in para)
		merged.append(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64))
		mscores.append(ps)
	return merged, mscores


def deduplicate_boxes(
	boxes: list[np.ndarray],
	scores: list[float],
	iou_thresh: float = 0.40,
) -> tuple[list[np.ndarray], list[float]]:
	"""MERGE OR DEDUPLICATE OVERLAPPING PARAGRAPH BOXES (e.g. WHEN BOTH COMIC DETECTOR AND
	RAPIDOCR DETECT THE SAME PARAGRAPH REGION WITH SLIGHTLY DIFFERENT BOUNDS).

	IF TWO BOXES HAVE IoU >= iou_thresh OR ONE BOX ENCLOSES ≥ 60% OF THE OTHER,
	TAKE THEIR AXIS-ALIGNED UNION SO ALL OCR LINES ARE MATCHED BY A SINGLE REGION BOX.
	"""
	if not boxes:
		return [], []

	indexed = sorted(enumerate(zip(boxes, scores)), key=lambda item: item[1][1], reverse=True)
	kept_boxes: list[np.ndarray] = []
	kept_scores: list[float] = []

	for _idx, (box, score) in indexed:
		merged = False
		x0, y0, w, h = box_to_xywh(box)
		box_area = max(1.0, float(w * h))
		for k in range(len(kept_boxes)):
			kbox = kept_boxes[k]
			kx0, ky0, kw, kh = box_to_xywh(kbox)
			karea = max(1.0, float(kw * kh))

			iou = box_iou(box, kbox)
			ix = max(0.0, min(float(x0 + w), float(kx0 + kw)) - max(float(x0), float(kx0)))
			iy = max(0.0, min(float(y0 + h), float(ky0 + kh)) - max(float(y0), float(ky0)))
			inter = ix * iy
			min_area = min(box_area, karea)
			max_area = max(box_area, karea)
			overlap_ratio = inter / min_area if min_area > 0 else 0.0

			if iou >= iou_thresh or overlap_ratio >= 0.70 or (overlap_ratio >= 0.60 and max_area / min_area <= 2.5):
				ux0 = min(x0, kx0)
				uy0 = min(y0, ky0)
				ux1 = max(x0 + w, kx0 + kw)
				uy1 = max(y0 + h, ky0 + kh)
				kept_boxes[k] = np.array([[ux0, uy0], [ux1, uy0], [ux1, uy1], [ux0, uy1]], dtype=np.float64)
				kept_scores[k] = max(kept_scores[k], score)
				merged = True
				break
		if not merged:
			kept_boxes.append(box)
			kept_scores.append(score)

	return kept_boxes, kept_scores


def sort_regions_top_to_bottom(boxes: list[np.ndarray], page_h: int, row_tolerance: float = 0.5) -> list[int]:
	"""READING ORDER: GROUP INTO ROWS BY VERTICAL OVERLAP, THEN LEFT-TO-RIGHT WITHIN EACH ROW.

	RETURNS THE INDICES OF `boxes` IN READING ORDER. A NEW ROW STARTS WHEN A REGION'S VERTICAL
	CENTER FALLS BELOW THE CURRENT ROW'S BOTTOM (EXPANDED BY `row_tolerance` × ITS HEIGHT).
	"""
	if not boxes:
		return []
	centers = []
	for b in boxes:
		x, y, w, h = box_to_xywh(b)
		centers.append((y + h / 2, x + w / 2, h))
	rows: list[list[int]] = []
	for i, (cy, cx, h) in enumerate(centers):
		placed = False
		for row in rows:
			ys = [centers[j][0] for j in row]
			hs = [centers[j][2] for j in row]
			top = min(ys) - max(hs) * row_tolerance
			bottom = max(ys) + max(hs) * row_tolerance
			if top <= cy <= bottom:
				row.append(i)
				placed = True
				break
		if not placed:
			rows.append([i])
	# ROWS ARE CREATED IN INPUT ORDER — SORT THEM BY VERTICAL POSITION (THE TOPMOST ROW FIRST)
	rows.sort(key=lambda row: min(centers[j][0] for j in row))
	order: list[int] = []
	for row in rows:
		order.extend(sorted(row, key=lambda j: centers[j][1]))
	return order


@dataclass
class DetectResult:
	"""FINAL DETECTOR OUTPUT — BOXES IN ORIGINAL PIXELS + THE TEXT MASK AT ORIGINAL RESOLUTION."""

	boxes: list[np.ndarray] = field(default_factory=list)  # (4, 2) INT, ORIGINAL COORDS
	scores: list[float] = field(default_factory=list)
	mask: np.ndarray | None = None  # uint8 (H, W) — 255 WHERE TEXT PROBABILITY WAS HIGH
	backend: str = "comic-ctd"


class ComicTextDetector:
	"""ONNX RUNTIME WRAPPER — THE SAME PRE/POST-PIPELINE AS ctd.py, CPU-ONLY BY DEFAULT."""

	def __init__(self, model_path: str | None = None, input_size: int = config.DETECT_INPUT_SIZE) -> None:
		self.model_path = model_path or str(config.DETECT_MODEL_PATH)
		self.input_size = input_size
		self._session = None
		self._load_lock = threading.Lock()

	def available(self) -> bool:
		from pathlib import Path

		return Path(self.model_path).exists()

	def _load(self):
		# DOUBLE-CHECKED LOCKING — CONCURRENT /pages/analyze CALLS SHARE ONE SESSION (ORT SESSIONS
		# SUPPORT CONCURRENT Run() CALLS; TWO SESSIONS WOULD DOUBLE THE ~90MB MODEL IN RAM).
		if self._session is None:
			with self._load_lock:
				if self._session is None:
					import onnxruntime as ort

					self._session = ort.InferenceSession(
						self.model_path, providers=config.ORT_PROVIDERS, sess_options=ort.SessionOptions()
					)

	def analyze(self, img_bgr: np.ndarray) -> DetectResult:
		"""RUN THE FULL PIPELINE ON ONE PAGE (BGR NUMPY IMAGE)."""
		im_h, im_w = img_bgr.shape[:2]
		tensor, (dw, dh) = preprocess_for_onnx(img_bgr, self.input_size)

		self._load()
		assert self._session is not None
		outputs = self._session.run(None, {self._session.get_inputs()[0].name: tensor})
		# [0] = YOLO BLOBS (UNUSED — SAME AS UPSTREAM), [1] = MASK, [2] = LINES_MAP
		#
		# EMPIRICALLY THE beta-0.3 EXPORT OUTPUTS AT *FULL INPUT RESOLUTION* (NOT 256×256):
		#   mask  → (1, 1024, 1024), lines → (1, 2, 1024, 1024)
		# UPSTREAM'S SegDetectorRepresenter TAKES `pred[:, 0, :, :]` — CHANNEL 0 OF lines IS THE DB MAP.
		mask_raw = np.squeeze(outputs[1])
		lines_raw = np.squeeze(outputs[2])
		if lines_raw.ndim == 3 and lines_raw.shape[0] > 1:
			lines_raw = lines_raw[0]  # DROP THE EXTRA CHANNEL(S) — DB MAP IS FIRST
		if mask_raw.ndim != 2 or lines_raw.ndim != 2:
			raise ValueError(f"unexpected detector output shapes: mask={mask_raw.shape} lines={lines_raw.shape}")

		mask = crop_padded(mask_raw, dw, dh)
		lines_map = crop_padded(lines_raw, dw, dh)
		mask = (mask * 255).astype(np.uint8)

		boxes, scores = lines_map_to_boxes(
			lines_map,
			dest_width=im_w,
			dest_height=im_h,
			thresh=config.DB_THRESH,
			box_thresh=config.DB_BOX_THRESH,
			unclip_ratio=config.DB_UNCLIP_RATIO,
			max_candidates=config.DB_MAX_CANDIDATES,
			min_side=config.MIN_REGION_SIDE,
		)
		mask = cv2.resize(mask, (im_w, im_h), interpolation=cv2.INTER_LINEAR)
		return DetectResult(boxes=boxes, scores=scores, mask=mask, backend="comic-ctd")
