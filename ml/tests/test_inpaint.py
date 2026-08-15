# INPAINTING TESTS — MASK BUILDING + LAMA ONNX SESSION + FACTORY AND ERROR HANDLING.
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from app import config, inpaint
from app.inpaint import Inpainter, build_mask, get_inpainter, polygon_from_box
from app.lama import LamaInpainter


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


class TestLamaOnnxInpainter:
	def test_onnx_inpainter_call(self, monkeypatch):
		mock_session = MagicMock()
		mock_session.get_inputs.return_value = [
			MagicMock(name="image", shape=[1, 3, 64, 64]),
			MagicMock(name="mask", shape=[1, 1, 64, 64]),
		]
		mock_session.get_inputs.return_value[0].name = "image"
		mock_session.get_inputs.return_value[1].name = "mask"

		# Mock output matching (1, 3, H_padded, W_padded)
		def fake_run(output_names, input_feed):
			img_tensor = input_feed["image"]
			return [img_tensor]

		mock_session.run.side_effect = fake_run

		monkeypatch.setattr("onnxruntime.InferenceSession", lambda *args, **kwargs: mock_session)

		inpainter = LamaInpainter("dummy_path.onnx")
		img = np.full((60, 60, 3), 200, dtype=np.uint8)
		mask = np.zeros((60, 60), dtype=np.uint8)
		mask[10:20, 10:20] = 255

		# Default / patch mode
		out_patch = inpainter(img, mask, mode="patch")
		assert out_patch.shape == (60, 60, 3)
		assert out_patch.dtype == np.uint8

		# Scaled mode
		out_scaled = inpainter(img, mask, mode="scaled")
		assert out_scaled.shape == (60, 60, 3)
		assert out_scaled.dtype == np.uint8

		# Full dynamic mode
		out_full = inpainter(img, mask, mode="full")
		assert out_full.shape == (60, 60, 3)
		assert out_full.dtype == np.uint8



class TestInpainterUnsupported:
	def test_raises_runtime_error_when_unsupported(self):
		inpainter = Inpainter(backend="unsupported")
		assert not inpainter.available()
		img = np.full((50, 50, 3), 255, dtype=np.uint8)
		mask = np.ones((50, 50), dtype=np.uint8) * 255
		with pytest.raises(RuntimeError, match="Inpainting is not supported"):
			inpainter(img, mask)


class TestInpainterFactory:
	def test_returns_unsupported_when_weights_missing(self, monkeypatch):
		monkeypatch.setattr(config, "LAMA_MODEL_PATH", config.MODELS_DIR / "definitely-missing.onnx")
		monkeypatch.setattr(inpaint, "_lama_model", None)
		monkeypatch.setattr(inpaint, "_lama_ready", threading.Event())
		assert get_inpainter().backend == "unsupported"
		assert not get_inpainter().available()

	def test_factory_returns_inpainter_instance(self):
		assert isinstance(get_inpainter(), Inpainter)

	def test_concurrent_loaders_synchronize_load(self, monkeypatch):
		from app import lama

		class SlowFakeLama:
			def __init__(self, path: str) -> None:
				time.sleep(0.3)
				self.path = path

		from pathlib import Path

		monkeypatch.setattr(config, "LAMA_MODEL_PATH", config.MODELS_DIR / "test_fake.onnx")
		monkeypatch.setattr(Path, "exists", lambda self: True)
		monkeypatch.setattr(inpaint, "_lama_model", None)
		monkeypatch.setattr(inpaint, "_lama_ready", threading.Event())
		monkeypatch.setattr(lama, "LamaInpainter", SlowFakeLama)

		results: list[object] = []
		barrier = threading.Barrier(5)

		def worker() -> None:
			barrier.wait()
			results.append(inpaint._get_lama())

		threads = [threading.Thread(target=worker) for _ in range(5)]
		for t in threads:
			t.start()
		for t in threads:
			t.join()

		assert all(r is not None for r in results)
		assert len({id(r) for r in results}) == 1
