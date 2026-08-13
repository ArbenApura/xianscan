# INPAINTING — TEXT ERASURE ON THE CLEANED PAGE VIA LaMa ONNX.
#
# RUNS LaMa ONNX DIRECTLY ON CPU/GPU VIA onnxruntime.
# NOTE: THE DEGRADED OpenCV TELEA FALLBACK HAS BEEN REMOVED — IF THE ONNX WEIGHTS ARE NOT
# PRESENT, INPAINTING IS MARKED AS "unsupported" AND EXPLICIT ERRORS ARE RAISED INSTEAD OF
# SILENTLY CORRUPTING ARTWORK.
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import config

logger = logging.getLogger(__name__)

# -- TYPES -- #


@dataclass
class Inpainter:
	backend: str

	def available(self) -> bool:
		return self.backend == "lama-onnx"

	def __call__(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
		"""FILL `mask` REGIONS (255 = ERASE) IN img_bgr. RETURNS A NEW IMAGE."""
		if not self.available():
			raise RuntimeError(
				"Inpainting is not supported: LaMa ONNX model is not available. "
				"Please run `python scripts/download_models.py` to download models/lama.onnx."
			)
		return _lama_inpaint(img_bgr, mask)


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
_lama_lock = threading.Lock()
_lama_ready = threading.Event()  # SET ONCE THE FIRST LOAD ATTEMPT COMPLETES (SUCCESS OR FAILURE)


def available_backend() -> str:
	"""WEIGHTS-PRESENCE CHECK WITHOUT LOADING — /health MUST STAY FAST."""
	return "lama-onnx" if config.LAMA_MODEL_PATH.exists() else "unsupported"


def _get_lama():
	"""LAZY-LOAD THE ONNX MODEL ONCE. RETURNS None IF THE WEIGHTS ARE MISSING OR FAIL TO LOAD."""
	global _lama_model
	if _lama_ready.is_set():
		return _lama_model
	with _lama_lock:
		if not _lama_ready.is_set():
			try:
				if config.LAMA_MODEL_PATH.exists():
					from .lama import LamaInpainter

					_lama_model = LamaInpainter(str(config.LAMA_MODEL_PATH))
			except Exception as e:
				logger.warning("Failed to initialize LaMa ONNX inpainter: %s", e)
				_lama_model = None
			_lama_ready.set()
	return _lama_model


def _lama_inpaint(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
	model = _get_lama()
	if model is None:
		raise RuntimeError(
			"Inpainting is not supported: LaMa ONNX model is not available. "
			"Please run `python scripts/download_models.py` to download models/lama.onnx."
		)
	return model(img_bgr, mask)


def get_inpainter() -> Inpainter:
	"""FACTORY — RETURNS Inpainter (backend='lama-onnx' WHEN MODEL EXISTS, 'unsupported' OTHERWISE)."""
	if config.LAMA_MODEL_PATH.exists():
		return Inpainter(backend="lama-onnx")
	return Inpainter(backend="unsupported")
