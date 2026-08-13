# ORCHESTRATION — THE PURE PIPELINE THE FASTAPI ROUTES CALL. BACKENDS ARE MODULE-LEVEL SINGLETONS SO
# TESTS CAN MONKEYPATCH THEM WITHOUT TOUCHING THE HTTP LAYER.
from __future__ import annotations

import cv2
import numpy as np

from . import detect, ocr
from .inpaint import build_mask, get_inpainter, polygon_from_box
from .schemas import AnalyzeResponse, Box, CleanRequestRegion, Region
from .watermark import watermark_remover

# -- BACKEND SINGLETONS (REPLACEABLE IN TESTS) -- #

detector = detect.ComicTextDetector()


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
    return Region(
        id=f"r{index}",
        box=Box(x=x, y=y, w=w, h=h),
        polygon=polygon,
        category=detect.classify_region(box, page_w, page_h),  # type: ignore[arg-type]
        confidence=0.0,
        vertical=detect.is_vertical_box(box),
    )


def analyze_image(img_bgr: np.ndarray) -> AnalyzeResponse:
	"""DETECT (COMIC ∪ RAPIDOCR) → MERGE SAME-LINE BOXES → OCR → ORDER → SHAPE.

	THE HYBRID UNION FIXES THE COMIC DETECTOR'S BLIND SPOTS (VERIFIED ON THE REAL MODEL):
	  - ONE LINE SPLIT AT A LINE-MAP DIP (r1+r2) → merge_text_lines JOINS THE FRAGMENTS
	  - TEXT THE COMIC MODEL MISSES (WEAK/ZERO LINE-MAP SIGNAL, e.g. 你好， AT LINE START) →
	    RAPIDOCR'S GENERAL-TEXT DETECTOR SEES IT, AND THE UNION TAKES THE WIDER BOX
	"""
	page_h, page_w = img_bgr.shape[:2]

	comic_boxes: list[np.ndarray] = []
	comic_scores: list[float] = []
	backend = "rapidocr-fallback"
	if detector.available():
		result = detector.analyze(img_bgr)
		comic_boxes = result.boxes
		comic_scores = result.scores
		backend = result.backend

	# RAPIDOCR FULL-PAGE DET+REC — ALWAYS RUN (THE UNION'S SECOND OPINION + TEXT SOURCE)
	rapid_lines = ocr.recognize_full(img_bgr)

	all_boxes = comic_boxes + [pts for pts, _t, _s in rapid_lines]
	all_scores = comic_scores + [float(s) for _pts, _t, s in rapid_lines]
	boxes, _scores = detect.merge_text_lines(all_boxes, all_scores)
	# PARAGRAPH GROUPING: A MULTI-LINE BUBBLE'S LINES BECOME ONE REGION (ONE TRANSLATION, ONE BLOCK)
	boxes, _scores = detect.group_paragraphs(boxes, _scores)

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
			(line, text, score)
			for line, text, score in rapid_lines
			if detect.line_center_inside(line, box)
		]
		if matched:
			matched.sort(key=lambda m: (m[0][:, 1].min(), m[0][:, 0].min()))
			region.text = "\n".join(t for _l, t, _s in matched if t.strip())
			region.confidence = float(max(s for _l, _t, s in matched)) if matched else 0.0
			# USE THE CONVEX HULL OF ALL MATCHED RAPID-LINE CORNER POINTS AS:
			#   1) THE INPAINT POLYGON — TIGHTER THAN THE GROUPED AABB UNION.
			#   2) THE BASIS FOR region.box AND region.category — THE RAPIDOCR LINE POLYGONS
			#      ARE TIGHT AROUND ACTUAL TEXT, SO THEIR HULL'S BOUNDING BOX IS THE REAL TEXT
			#      EXTENT. THE ORIGINAL GROUPED BOX IS THE AABB UNION (OFTEN MUCH WIDER), WHICH
			#      CAUSES classify_region TO MISFIRE AND THE TYPESETTER TO PICK ENORMOUS FONTS.
			all_pts = np.vstack([line.reshape(-1, 2) for line, _, _ in matched]).astype(np.float32)
			hull = cv2.convexHull(all_pts)
			if hull is not None and len(hull) >= 3:
				hull_pts = hull.reshape(-1, 2).astype(np.float64)
				region.polygon = [[int(p[0]), int(p[1])] for p in hull_pts]
				# REDERIVE BOX AND CATEGORY FROM THE HULL'S BOUNDING BOX
				hx, hy, hw, hh = detect.box_to_xywh(hull_pts)
				region.box = Box(x=hx, y=hy, w=max(1, hw), h=max(1, hh))
				region.category = detect.classify_region(hull_pts, page_w, page_h)  # type: ignore[arg-type]
				region.vertical = detect.is_vertical_box(hull_pts)
		else:
			ocr_result = ocr.recognize_crop(ocr.crop_region(img_bgr, box))
			if ocr_result:
				region.text = ocr_result.text
				region.confidence = ocr_result.score
		regions.append(region)
	return AnalyzeResponse(width=page_w, height=page_h, regions=regions, backend=backend)


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


