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


def classify_region(box: np.ndarray, page_w: int, page_h: int) -> str:
	"""CATEGORY HEURISTIC — SFX/IMPACT TEXT HAS BIG GLYPHS: A SINGLE TALL BLOCK OR A WIDE **AND**
	VERY TALL BLOCK. A MULTI-LINE PARAGRAPH IS TALL BUT ITS LINES ARE SMALL — IT MUST STAY DIALOGUE
	(THE OLD `h > page_h * 0.1` RULE MISLABELED 3-LINE BUBBLES AS SFX).

	PURE + TESTED; THE WEB APP CAN OVERRIDE PER-REGION LATER.
	"""
	_x, _y, w, h = box_to_xywh(box)
	if h > page_h * 0.3 or (w > page_w * 0.45 and h > page_h * 0.2):
		return "sfx"
	return "dialogue"


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


def merge_text_lines(
	boxes: list[np.ndarray],
	scores: list[float],
	gap_factor: float = 1.0,
	overlap_min: float = 0.5,
) -> tuple[list[np.ndarray], list[float]]:
	"""MERGE HORIZONTAL TEXT BOXES THAT SIT ON THE SAME LINE (PORT OF manga-image-translator's
	textline_merge CONCEPT — THE DB REPRESENTER SPLITS ONE LINE WHEREVER THE LINE MAP DIPS).

	RULE (PURE — UNIT-TESTED):
	  - SAME LINE: VERTICAL OVERLAP ≥ overlap_min × min(heights)
	  - MERGE WHEN: HORIZONTAL GAP ≤ gap_factor × max(heights) — A NEGATIVE GAP (OVERLAP) ALWAYS MERGES
	  - VERTICAL TEXT COLUMNS (h > 1.2×w) ARE NEVER MERGED — SIDE-BY-SIDE COLUMNS LOOK IDENTICAL
	    TO SAME-LINE BOXES BY THIS RULE AND MUST STAY SEPARATE REGIONS
	RETURNS (merged_boxes, merged_scores) — EACH MERGED BOX IS THE UNION (AXIS-ALIGNED), SCORE = MAX.
	"""
	if not boxes:
		return [], []
	items = sorted(zip(boxes, scores), key=lambda p: p[0][:, 0].min())
	lines: list[list] = []  # [x0, y0, x1, y1, score]
	for box, score in items:
		x, y, w, h = box_to_xywh(box)
		x1, y1 = x + w, y + h
		if h > w * 1.2:
			# VERTICAL COLUMN — ITS OWN LINE, NEVER MERGED
			lines.append([x, y, x1, y1, score])
			continue
		placed = False
		for ln in lines:
			lx0, ly0, lx1, ly1, lscore = ln
			min_h = min(h, ly1 - ly0)
			overlap = min(y1, ly1) - max(y, ly0)
			if overlap < overlap_min * min_h:
				continue
			if x - lx1 <= gap_factor * max(h, ly1 - ly0):
				ln[0] = min(lx0, x)
				ln[1] = min(ly0, y)
				ln[2] = max(lx1, x1)
				ln[3] = max(ly1, y1)
				ln[4] = max(lscore, score)
				placed = True
				break
		if not placed:
			lines.append([x, y, x1, y1, score])
	merged = []
	mscores = []
	for x0, y0, x1, y1, score in lines:
		merged.append(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64))
		mscores.append(score)
	return merged, mscores


def group_paragraphs(
	boxes: list[np.ndarray],
	scores: list[float],
	overlap_min: float = 0.35,
	gap_factor: float = 0.3,
) -> tuple[list[np.ndarray], list[float]]:
	"""GROUP VERTICALLY STACKED TEXT LINES INTO PARAGRAPHS (A MULTI-LINE SPEECH BUBBLE).

	THE DETECTORS EMIT ONE BOX PER LINE; A 3-LINE BUBBLE BECOMES 3 REGIONS → 3 SEPARATE
	TRANSLATIONS + 3 SCATTERED TYPESET LINES (THE "TRASH" OUTPUT). THIS JOINS LINES THAT
	BELONG TO THE SAME BUBBLE/PARAGRAPH:
	  - SAME PARAGRAPH: THE NEXT LINE'S X-RANGE OVERLAPS THE PREVIOUS LINE'S BY ≥ overlap_min
	    × min(widths) (BUBBLE LINES ARE ROUGHLY CENTER-ALIGNED), AND
	  - THE VERTICAL GAP BETWEEN THEM IS ≤ gap_factor × min(heights) (BUBBLE LEADING IS TIGHT;
	    THE GAP BETWEEN SEPARATE BUBBLES IS LARGER).
	VERTICAL TEXT COLUMNS (h > 1.2×w) NEVER GROUP — EACH COLUMN IS ITS OWN PARAGRAPH.
	RETURNS (paragraph_boxes, scores) — THE UNION BOX PER PARAGRAPH; STANDALONE LINES UNCHANGED.
	"""
	if not boxes:
		return [], []
	# VERTICAL COLUMNS ARE THEIR OWN PARAGRAPHS (NEVER GROUPED); HORIZONTAL LINES GROUP BY GEOMETRY
	paragraphs: list[list[np.ndarray]] = []
	para_scores: list[float] = []
	for box, score in zip(boxes, scores):
		x, y, w, h = box_to_xywh(box)
		if h > w * 1.2:
			paragraphs.append([box])
			para_scores.append(score)
	horizontal = sorted(
		((b, s) for b, s in zip(boxes, scores) if box_to_xywh(b)[3] <= box_to_xywh(b)[2] * 1.2),
		key=lambda p: (box_to_xywh(p[0])[1], box_to_xywh(p[0])[0]),
	)
	for box, score in horizontal:
		x, y, w, h = box_to_xywh(box)
		x1 = x + w
		placed = False
		for para, _ps in zip(paragraphs, para_scores):
			last = para[-1]
			lx, ly, lw, lh = box_to_xywh(last)
			lx1 = lx + lw
			# VERTICAL CONTIGUITY: THE NEW LINE SITS AT OR BELOW THE PARAGRAPH'S BOTTOM LINE.
			# A NEGATIVE GAP (SLIGHT BOX OVERLAP FROM PADDING/LEADING) IS NORMAL FOR STACKED LINES
			# AND MUST NOT BLOCK GROUPING — THE X-OVERLAP CHECK BELOW KEEPS SIDE-BY-SIDE LINES OUT.
			gap = y - (ly + lh)
			if gap > gap_factor * min(h, lh):
				continue
			# HORIZONTAL ALIGNMENT: X-RANGES OVERLAP LIKE CENTERED BUBBLE LINES
			overlap = min(x1, lx1) - max(x, lx)
			if overlap < overlap_min * min(w, lw):
				continue
			para.append(box)
			placed = True
			break
		if not placed:
			paragraphs.append([box])
			para_scores.append(score)

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

	def available(self) -> bool:
		from pathlib import Path

		return Path(self.model_path).exists()

	def _load(self):
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
