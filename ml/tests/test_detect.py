# DETECTOR PURE-FUNCTION TESTS — LETTERBOX MATH, ONNX TENSOR SHAPE, DB REPRESENTER, ORDERING,
# CLASSIFICATION. NO MODEL WEIGHTS REQUIRED (ALL SYNTHETIC ARRAYS).
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.detect import (
	box_score_fast,
	box_to_xywh,
	classify_region,
	crop_padded,
	get_mini_boxes,
	group_paragraphs,
	is_vertical_box,
	letterbox,
	lines_map_to_boxes,
	merge_text_lines,
	preprocess_for_onnx,
	sort_regions_top_to_bottom,
	unclip_polygon,
)


def contour_area(pts: np.ndarray) -> float:
	return float(cv2.contourArea(pts.astype(np.float32)))


class TestLetterbox:
	def test_square_input_scales_up_exactly(self):
		img = np.zeros((512, 512, 3), dtype=np.uint8)
		out, ratio, (dw, dh) = letterbox(img, new_shape=(1024, 1024))
		assert out.shape == (1024, 1024, 3)
		assert ratio[0] == pytest.approx(2.0)
		assert (dw, dh) == (0, 0)  # 512×512 SCALES UP TO EXACTLY 1024×1024 — NO PAD

	def test_wide_input_pads_bottom(self):
		img = np.zeros((400, 800, 3), dtype=np.uint8)
		out, ratio, (dw, dh) = letterbox(img, new_shape=(1024, 1024))
		assert out.shape == (1024, 1024, 3)
		assert ratio[0] == pytest.approx(1.28)  # 1024/800
		# WIDTH FITS EXACTLY (800 * 1.28 = 1024) → ONLY VERTICAL PAD
		assert dw == 0
		assert dh == 1024 - int(round(400 * 1.28))

	def test_pad_color_is_black(self):
		# 100×300 → r = 256/300 ≈ 0.853 → 256×85 CONTENT + 171 ROWS OF BOTTOM PAD
		img = np.full((100, 300, 3), 255, dtype=np.uint8)
		out, _r, (dw, dh) = letterbox(img, new_shape=(256, 256))
		assert (dw, dh) == (0, 171)
		assert tuple(out[255, 0]) == (0, 0, 0)  # BOTTOM-EDGE PAD ROW IS BLACK
		assert tuple(out[84, 0]) == (255, 255, 255)  # LAST CONTENT ROW IS THE IMAGE


class TestPreprocessForOnnx:
	def test_tensor_shape_and_normalization(self):
		img = np.zeros((100, 200, 3), dtype=np.uint8)
		tensor, (dw, dh) = preprocess_for_onnx(img, input_size=1024)
		assert tensor.shape == (1, 3, 1024, 1024)
		assert tensor.dtype == np.float32
		assert tensor.min() >= 0.0 and tensor.max() <= 1.0

	def test_channel_order_is_bgr(self):
		# A PURE-RED PIXEL (BGR: [0, 0, 255]) MUST LAND IN CHANNEL 2 OF THE TENSOR, NOT CHANNEL 0.
		# AFTER cvtColor BGR→RGB THE B CHANNEL IS ALL ZEROS, SO THE *TOTAL* CHANNEL ENERGY PINS THE ORDER
		# (cv2's BILINEAR RESAMPLE SPREADS A SINGLE HOT PIXEL — PER-PIXEL EXACTNESS WOULD BE BRITTLE).
		img = np.zeros((10, 10, 3), dtype=np.uint8)
		img[5, 5] = (0, 0, 255)
		tensor, _ = preprocess_for_onnx(img, input_size=32)
		assert tensor[0, 0].sum() == 0.0  # CHANNEL 0 (B) — NOTHING
		assert tensor[0, 2].sum() > 0.0  # CHANNEL 2 (R) — THE HOT PIXEL


class TestCropPadded:
	def test_strips_bottom_right_pad(self):
		m = np.ones((16, 16), dtype=np.float32)
		assert crop_padded(m, pad_w=2, pad_h=4).shape == (12, 14)


class TestDbRepresenter:
	def test_box_score_fast_mean_inside_contour(self):
		bitmap = np.zeros((40, 40), dtype=np.float32)
		box = np.array([[12, 12], [28, 12], [28, 28], [12, 28]], dtype=np.float64)
		# FILL THE SAME CONTOUR AT 0.8 — THE MEAN OVER THE CONTOUR MUST BE EXACTLY 0.8 (cv2.fillPoly
		# RASTERIZES THE SAME PIXELS THE MEASUREMENT MASKS, SO THE EDGE EFFECT CANCELS).
		cv2.fillPoly(bitmap, [box.astype(np.int32).reshape(-1, 1, 2)], 0.8)
		assert box_score_fast(bitmap, box) == pytest.approx(0.8)

	def test_unclip_expands_polygon(self):
		box = np.array([[10, 10], [30, 10], [30, 20], [10, 20]], dtype=np.float64)
		expanded = unclip_polygon(box, unclip_ratio=2.0)
		assert expanded is not None
		assert contour_area(expanded) > contour_area(box)

	def test_get_mini_boxes_returns_four_points(self):
		box, sside = get_mini_boxes(np.array([[0, 0], [40, 0], [40, 10], [0, 10]], dtype=np.float32))
		assert len(box) == 4
		assert sside == pytest.approx(10.0)

	def test_lines_map_to_boxes_detects_one_region_scaled_to_dest(self):
		# 64×64 PROBABILITY MAP WITH A HOT 16×16 BLOCK IN THE TOP-LEFT QUADRANT
		lines = np.zeros((64, 64), dtype=np.float32)
		lines[8:24, 8:24] = 0.9
		boxes, scores = lines_map_to_boxes(lines, dest_width=800, dest_height=1200)
		assert len(boxes) == 1
		assert len(scores) == 1
		x, y, w, h = box_to_xywh(boxes[0])
		# 8..23 IN A 64-MAP → 100..300 IN AN 800-WIDE DEST (×12.5), THEN UNCLIP (ratio 1.5) EXPANDS
		# ~6 MAP PIXELS EACH SIDE: 2..30 → x≈25, y≈37.5, w≈350, h≈337
		assert 15 <= x <= 35
		assert 25 <= y <= 50
		assert w >= 320 and h >= 300

	def test_lines_map_to_boxes_filters_low_score(self):
		lines = np.zeros((64, 64), dtype=np.float32)
		lines[8:24, 8:24] = 0.2  # BELOW DB_THRESH (0.3) → NOTHING
		boxes, _ = lines_map_to_boxes(lines, dest_width=800, dest_height=1200)
		assert boxes == []

	def test_lines_map_to_boxes_accepts_4d_input(self):
		lines = np.zeros((1, 1, 64, 64), dtype=np.float32)
		lines[0, 0, 8:24, 8:24] = 0.9
		boxes, _ = lines_map_to_boxes(lines, dest_width=800, dest_height=1200)
		assert len(boxes) == 1


class TestBoxGeometry:
	def test_box_to_xywh(self):
		box = np.array([[10, 20], [110, 20], [110, 80], [10, 80]], dtype=np.float64)
		assert box_to_xywh(box) == (10, 20, 100, 60)

	def test_is_vertical_box(self):
		tall = np.array([[0, 0], [20, 0], [20, 100], [0, 100]], dtype=np.float64)
		wide = np.array([[0, 0], [100, 0], [100, 20], [0, 20]], dtype=np.float64)
		assert is_vertical_box(tall) is True
		assert is_vertical_box(wide) is False

	def test_classify_region(self):
		# SFX: WIDE **AND** VERY TALL (h=260 > 0.2×1200, w=500 > 0.45×800)
		sfx = np.array([[0, 0], [500, 0], [500, 260], [0, 260]], dtype=np.float64)
		dialogue = np.array([[10, 10], [110, 10], [110, 60], [10, 60]], dtype=np.float64)
		# A FULL-WIDTH LINE OF NORMAL HEIGHT (THE MERGED UNION OF ONE LONG LINE) IS DIALOGUE
		wide_line = np.array([[0, 0], [500, 0], [500, 90], [0, 90]], dtype=np.float64)
		# A 4-LINE PARAGRAPH IS TALL BUT ITS LINES ARE SMALL — DIALOGUE, NOT SFX
		paragraph = np.array([[80, 500], [500, 500], [500, 700], [80, 700]], dtype=np.float64)
		assert classify_region(sfx, page_w=800, page_h=1200) == "sfx"
		assert classify_region(dialogue, page_w=800, page_h=1200) == "dialogue"
		assert classify_region(wide_line, page_w=800, page_h=1200) == "dialogue"
		assert classify_region(paragraph, page_w=800, page_h=1200) == "dialogue"


class TestMergeTextLines:
	def box(self, x, y, w, h):
		return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)

	def test_merges_overlapping_same_line_boxes(self):
		# THE REAL r1+r2 CASE: THE DB SPLIT ONE LINE AT A PROBABILITY DIP — THE BOXES EVEN OVERLAP
		b1 = self.box(94, 301, 253, 69)
		b2 = self.box(328, 302, 210, 65)
		merged, scores = merge_text_lines([b1, b2], [0.74, 0.68])
		assert len(merged) == 1
		x, y, w, h = box_to_xywh(merged[0])
		assert (x, w, y, h) == (94, 444, 301, 69)  # THE UNION OF BOTH
		assert scores == [0.74]  # MAX SCORE

	def test_merges_adjacent_same_line_boxes_with_small_gap(self):
		b1 = self.box(100, 100, 200, 40)
		b2 = self.box(320, 105, 180, 38)  # GAP = 20 ≤ 40 (= max-height × 1.0)
		merged, _ = merge_text_lines([b1, b2], [0.7, 0.6])
		assert len(merged) == 1
		# UNION: x 100..500, y 100..143
		assert box_to_xywh(merged[0]) == (100, 100, 400, 43)

	def test_keeps_far_apart_same_line_boxes_separate(self):
		# TWO BUBBLES ON THE SAME ROW — GAP (300) > max-height (40) → NO MERGE
		b1 = self.box(100, 100, 200, 40)
		b2 = self.box(600, 105, 180, 38)
		merged, _ = merge_text_lines([b1, b2], [0.7, 0.6])
		assert len(merged) == 2

	def test_keeps_different_lines_separate(self):
		b1 = self.box(100, 100, 300, 40)
		b2 = self.box(150, 300, 300, 40)  # NO VERTICAL OVERLAP
		merged, _ = merge_text_lines([b1, b2], [0.7, 0.6])
		assert len(merged) == 2

	def test_never_merges_vertical_columns(self):
		# TWO SIDE-BY-SIDE VERTICAL COLUMNS: IDENTICAL Y-RANGE, ZERO X-GAP — MUST STAY SEPARATE
		c1 = self.box(100, 50, 20, 120)
		c2 = self.box(120, 50, 20, 120)
		merged, _ = merge_text_lines([c1, c2], [0.8, 0.8])
		assert len(merged) == 2

	def test_empty_input(self):
		assert merge_text_lines([], []) == ([], [])


class TestGroupParagraphs:
	def box(self, x, y, w, h):
		return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)

	def test_groups_two_stacked_lines_into_one_paragraph(self):
		# A TYPICAL 2-LINE BUBBLE: CENTER-ALIGNED LINES, TIGHT LEADING (gap=10 ≤ 0.75×50)
		l1 = self.box(150, 100, 300, 50)
		l2 = self.box(200, 160, 200, 50)
		merged, scores = group_paragraphs([l1, l2], [0.8, 0.7])
		assert len(merged) == 1
		assert box_to_xywh(merged[0]) == (150, 100, 300, 110)  # THE UNION

	def test_groups_lines_with_slight_box_overlap(self):
		# THE USER-PAGE REGRESSION: LINE 3 ENDS AT y=667, LINE 4 STARTS AT y=662 (BOX PADDING
		# OVERLAPS) — A NEGATIVE GAP MUST NOT BLOCK GROUPING (THE OLD RULE SPLIT THE PARAGRAPH).
		l3 = self.box(82, 621, 404, 46)
		l4 = self.box(83, 662, 419, 49)
		merged, _ = group_paragraphs([l3, l4], [0.8, 0.7])
		assert len(merged) == 1
		assert box_to_xywh(merged[0]) == (82, 621, 420, 90)  # THE UNION

	def test_keeps_separate_bubbles_separate(self):
		# TWO BUBBLES STACKED: THE GAP (gap=120) EXCEEDS 0.75×50 → NO GROUPING
		l1 = self.box(150, 100, 300, 50)
		l2 = self.box(160, 270, 280, 50)
		merged, _ = group_paragraphs([l1, l2], [0.8, 0.7])
		assert len(merged) == 2

	def test_keeps_side_by_side_lines_separate(self):
		# TWO LINES ON THE SAME ROW — NOT A PARAGRAPH (THE VERTICAL GAP IS NEGATIVE → NO GROUP)
		l1 = self.box(100, 100, 200, 40)
		l2 = self.box(400, 100, 200, 40)
		merged, _ = group_paragraphs([l1, l2], [0.8, 0.7])
		assert len(merged) == 2

	def test_requires_horizontal_overlap(self):
		# STACKED BUT NOT ALIGNED (DIFFERENT COLUMNS) → SEPARATE PARAGRAPHS
		l1 = self.box(100, 100, 200, 40)
		l2 = self.box(500, 150, 200, 40)
		merged, _ = group_paragraphs([l1, l2], [0.8, 0.7])
		assert len(merged) == 2

	def test_never_groups_vertical_columns(self):
		c1 = self.box(100, 50, 20, 120)
		c2 = self.box(120, 180, 20, 120)
		merged, _ = group_paragraphs([c1, c2], [0.8, 0.7])
		assert len(merged) == 2

	def test_empty_input(self):
		assert group_paragraphs([], []) == ([], [])


class TestReadingOrder:
	def test_two_regions_same_row_then_one_below(self):
		boxes = [
			np.array([[500, 100], [700, 100], [700, 140], [500, 140]], dtype=np.float64),  # RIGHT, TOP ROW
			np.array([[100, 110], [300, 110], [300, 150], [100, 150]], dtype=np.float64),  # LEFT, TOP ROW
			np.array([[100, 900], [300, 900], [300, 940], [100, 940]], dtype=np.float64),  # BELOW
		]
		order = sort_regions_top_to_bottom(boxes, page_h=1200)
		assert order == [1, 0, 2]  # TOP ROW LEFT→RIGHT, THEN THE LOWER ROW

	def test_empty_input(self):
		assert sort_regions_top_to_bottom([], page_h=1200) == []

	def test_rows_are_sorted_by_position_not_input_order(self):
		# INPUT ORDER IS [BOTTOM, TOP-LEFT, TOP-RIGHT] — THE OUTPUT MUST STILL BE TOP ROW FIRST
		boxes = [
			np.array([[100, 900], [300, 900], [300, 940], [100, 940]], dtype=np.float64),  # BELOW (INPUT 0)
			np.array([[100, 110], [300, 110], [300, 150], [100, 150]], dtype=np.float64),  # LEFT, TOP ROW
			np.array([[500, 100], [700, 100], [700, 140], [500, 140]], dtype=np.float64),  # RIGHT, TOP ROW
		]
		order = sort_regions_top_to_bottom(boxes, page_h=1200)
		assert order == [1, 2, 0]
