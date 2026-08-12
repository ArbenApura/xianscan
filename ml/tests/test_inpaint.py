# INPAINTING TESTS — MASK BUILDING (PURE) + THE OPENCV BACKEND ACTUALLY ERASING SYNTHETIC TEXT.
from __future__ import annotations

import numpy as np
import pytest

from app import config
from app.inpaint import Inpainter, build_mask, get_inpainter, polygon_from_box


class TestBuildMask:
	def test_fills_polygon(self):
		mask = build_mask(100, 100, [polygon_from_box(10, 10, 30, 20)], dilate_px=0)
		assert mask[15, 15] == 255
		assert mask[5, 5] == 0  # OUTSIDE
		assert mask[45, 25] == 0

	def test_dilate_expands_fill(self):
		# BOX SPANS x ∈ [50, 54] — 2PX DILATION EXTENDS TO x ∈ [48, 56]
		mask = build_mask(100, 100, [polygon_from_box(50, 50, 4, 4)], dilate_px=2)
		assert mask[50, 48] == 255  # EXACTLY 2PX OUTSIDE THE BOX EDGE (x=50)
		assert mask[50, 45] == 0  # 5PX OUTSIDE — UNTOUCHED

	def test_multiple_polygons_union(self):
		mask = build_mask(100, 100, [polygon_from_box(0, 0, 10, 10), polygon_from_box(80, 80, 10, 10)], dilate_px=0)
		assert mask[5, 5] == 255
		assert mask[85, 85] == 255
		assert mask[50, 50] == 0

	def test_empty_polygons_is_zero(self):
		assert not build_mask(100, 100, []).any()


class TestPolygonFromBox:
	def test_four_corners(self):
		poly = polygon_from_box(10, 20, 100, 50)
		assert poly.tolist() == [[10, 20], [110, 20], [110, 70], [10, 70]]


class TestOpenCvBackend:
	def test_erases_dark_text_on_white(self):
		# SYNTHETIC "TEXT": DARK PIXELS ON WHITE — AFTER INPAINT THE REGION MUST BE MOSTLY WHITE
		img = np.full((200, 200, 3), 255, dtype=np.uint8)
		img[80:120, 60:140] = (10, 10, 10)
		mask = build_mask(200, 200, [polygon_from_box(60, 80, 80, 40)], dilate_px=3)

		out = Inpainter(backend="opencv")(img, mask)

		region = out[80:120, 60:140]
		mean = region.mean()
		assert mean > 200, f"text region should be mostly white after inpainting, mean={mean}"

	def test_no_mask_returns_image(self):
		img = np.full((50, 50, 3), 128, dtype=np.uint8)
		mask = np.zeros((50, 50), dtype=np.uint8)
		out = Inpainter(backend="opencv")(img, mask)
		np.testing.assert_array_equal(out, img)


class TestInpainterFactory:
	def test_falls_back_to_opencv_when_weights_missing(self, monkeypatch):
		# THE SUITE RUNS WITHOUT MODELS — THE FACTORY MUST NEVER CRASH, ALWAYS RETURN AN INPAINTER.
		monkeypatch.setattr(config, "LAMA_MODEL_PATH", config.MODELS_DIR / "definitely-missing.pt")
		# RESET THE CACHED LOAD ATTEMPT SO THE MONKEYPATCHED PATH IS ACTUALLY CONSULTED
		from app import inpaint as inpaint_mod

		monkeypatch.setattr(inpaint_mod, "_lama_model", None)
		monkeypatch.setattr(inpaint_mod, "_lama_tried", False)
		assert get_inpainter().backend == "opencv"

	def test_factory_returns_inpainter_instance(self):
		assert isinstance(get_inpainter(), Inpainter)
