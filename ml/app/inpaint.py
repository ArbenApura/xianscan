# INPAINTING — TEXT ERASURE ON THE CLEANED PAGE.
#
# TWO BACKENDS, SELECTED AT PROCESS START:
#   'lama'   — LaMa big-lama (apache-2.0) VIA DIRECT TORCHSCRIPT INFERENCE (app/lama.py). BEST
#              QUALITY; ~200MB WEIGHTS IN models/big-lama.pt (download_models.py). FALLS BACK TO
#              OPENCV WHEN TORCH OR THE WEIGHTS ARE MISSING.
#   'opencv' — cv2.inpaint TELEA. ZERO EXTRA DEPS, FAST ON CPU, FINE FOR SOLID BUBBLES; THE
#              GUARANTEED FALLBACK ON EVERY MACHINE.
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import config

# -- TYPES -- #


@dataclass
class Inpainter:
	backend: str

	def __call__(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
		"""FILL `mask` REGIONS (255 = ERASE) IN img_bgr. RETURNS A NEW IMAGE."""
		if self.backend == "lama":
			return _lama_inpaint(img_bgr, mask)
		return _opencv_inpaint(img_bgr, mask)


# -- PURE HELPERS (UNIT-TESTED) -- #


def build_mask(height: int, width: int, polygons: list[np.ndarray], dilate_px: int = 3) -> np.ndarray:
	"""FILL EVERY REGION POLYGON INTO A uint8 MASK, THEN DILATE SO THE ERASURE SWALLOWS STROKES.

	POLYGONS ARE [[x, y], ...] IN IMAGE PIXELS; BOX FALLBACK IS DONE BY THE CALLER (RECT POLYGON).
	"""
	mask = np.zeros((height, width), dtype=np.uint8)
	for poly in polygons:
		pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
		cv2.fillPoly(mask, [pts], 255)
	if dilate_px > 0 and mask.any():
		kernel = np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), dtype=np.uint8)
		mask = cv2.dilate(mask, kernel, iterations=1)
	return mask


def polygon_from_box(x: int, y: int, w: int, h: int) -> np.ndarray:
	"""AXIS-ALIGNED BOX → 4-POINT POLYGON (THE CLEAN ENDPOINT'S BOX FALLBACK)."""
	return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)


# -- BACKENDS -- #

_lama_model = None
_lama_tried = False


def _get_lama():
	"""LAZY-LOAD THE TorchScript MODEL ONCE. RETURNS None IF TORCH OR THE WEIGHTS ARE MISSING."""
	global _lama_model, _lama_tried
	if _lama_tried:
		return _lama_model
	_lama_tried = True
	try:
		if not config.LAMA_MODEL_PATH.exists():
			return None
		from .lama import LamaInpainter

		_lama_model = LamaInpainter(str(config.LAMA_MODEL_PATH))
	except Exception:
		# TORCH MISSING / CORRUPT WEIGHTS / ANY LOAD PROBLEM → OPENCV FALLBACK
		_lama_model = None
	return _lama_model


def _lama_inpaint(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
	model = _get_lama()
	if model is None:
		return _opencv_inpaint(img_bgr, mask)
	return model(img_bgr, mask)


def _opencv_inpaint(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
	return cv2.inpaint(img_bgr, mask, 3, cv2.INPAINT_TELEA)


def get_inpainter() -> Inpainter:
	"""FACTORY — LAMA WHEN USABLE, OPENCV OTHERWISE (GUARANTEED NON-NONE)."""
	if _get_lama() is not None:
		return Inpainter(backend="lama")
	return Inpainter(backend="opencv")
