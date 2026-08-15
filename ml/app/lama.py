# LAMA INPAINTING — ONNX RUNTIME INFERENCE (APACHE-2.0).
#
# RUNS LaMa (LARGE MASK INPAINTING) DIRECTLY VIA onnxruntime WITHOUT REQUIRING PyTorch.
#
#   INPUT:  img_bgr (H, W, 3) uint8 BGR, mask (H, W) uint8 (255 = ERASE)
#   OUTPUT: img_bgr WITH THE MASKED REGIONS REBUILT
from __future__ import annotations

import cv2
import numpy as np
import onnxruntime as ort

from . import config

PAD_MOD = 8


class LamaInpainter:
	def __init__(self, model_path: str) -> None:
		opts = ort.SessionOptions()
		opts.inter_op_num_threads = 1
		opts.intra_op_num_threads = 4
		self.session = config.create_session(model_path, session_options=opts)
		inputs = self.session.get_inputs()
		self.img_name = inputs[0].name
		self.mask_name = inputs[1].name
		if len(inputs) >= 2 and len(inputs[0].shape) == 4 and inputs[0].shape[1] == 1:
			self.mask_name, self.img_name = inputs[0].name, inputs[1].name

		# CHECK IF INPUT SHAPE HAS FIXED SPATIAL DIMENSIONS (e.g. 512x512)
		img_shape = self.session.get_inputs()[0].shape
		if len(img_shape) == 4 and isinstance(img_shape[2], int) and isinstance(img_shape[3], int):
			self.fixed_size: tuple[int, int] | None = (img_shape[3], img_shape[2])  # (W, H)
		else:
			self.fixed_size = None

		self.model_path = model_path

	def _run_inference(self, x: np.ndarray, m: np.ndarray) -> list[np.ndarray]:
		try:
			return self.session.run(None, {self.img_name: x, self.mask_name: m})
		except Exception as e:
			opts = ort.SessionOptions()
			opts.inter_op_num_threads = 1
			opts.intra_op_num_threads = 4
			self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"], sess_options=opts)
			return self.session.run(None, {self.img_name: x, self.mask_name: m})

	def _inpaint_single_patch(self, img_rgb: np.ndarray, mask_bin: np.ndarray) -> np.ndarray:
		"""RUNS RAW INFERENCE ON A SINGLE RGB TENSOR PATCH (PADDED TO MODULO 8)."""
		orig_h, orig_w = img_rgb.shape[:2]
		pad_b = (PAD_MOD - orig_h % PAD_MOD) % PAD_MOD
		pad_r = (PAD_MOD - orig_w % PAD_MOD) % PAD_MOD

		if pad_b or pad_r:
			img_rgb = cv2.copyMakeBorder(img_rgb, 0, pad_b, 0, pad_r, cv2.BORDER_REPLICATE)
			mask_bin = cv2.copyMakeBorder(mask_bin, 0, pad_b, 0, pad_r, cv2.BORDER_CONSTANT, value=0)

		x = (img_rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
		m = mask_bin[None, None, :, :]

		outputs = self._run_inference(x, m)
		out = outputs[0][0]

		if out.ndim == 3 and out.shape[0] in (1, 3, 4):
			out = out.transpose(1, 2, 0)
		if out.max() <= 1.01:
			out = out * 255.0

		out = np.clip(out, 0, 255).astype(np.uint8)
		out = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)

		if pad_b or pad_r:
			out = out[:orig_h, :orig_w]
		return out

	def _inpaint_patch_mode(self, img_bgr: np.ndarray, mask: np.ndarray, pad: int = 24) -> np.ndarray:
		"""STRATEGY 3: LOCALIZED BOUNDING-BOX / BUBBLE PATCH INPAINTING (FASTEST + MAXIMUM NATIVE SHARPNESS)."""
		orig_h, orig_w = img_bgr.shape[:2]
		result = img_bgr.copy()

		# Find connected components of masked text regions to cluster nearby words
		num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
		if num_labels <= 1:
			return img_bgr

		for label in range(1, num_labels):
			bx = stats[label, cv2.CC_STAT_LEFT]
			by = stats[label, cv2.CC_STAT_TOP]
			bw = stats[label, cv2.CC_STAT_WIDTH]
			bh = stats[label, cv2.CC_STAT_HEIGHT]

			x0 = max(0, bx - pad)
			y0 = max(0, by - pad)
			x1 = min(orig_w, bx + bw + pad)
			y1 = min(orig_h, by + bh + pad)

			crop_h, crop_w = y1 - y0, x1 - x0
			if crop_h <= 0 or crop_w <= 0:
				continue

			crop_bgr = img_bgr[y0:y1, x0:x1]
			crop_mask = mask[y0:y1, x0:x1]
			if not np.any(crop_mask):
				continue

			crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
			crop_m_bin = (crop_mask > 0).astype(np.float32)

			out_crop_bgr = self._inpaint_single_patch(crop_rgb, crop_m_bin)

			# Alpha composite localized patch into full canvas
			mc_weight = (crop_mask.astype(np.float32) / 255.0)[:, :, None]
			result[y0:y1, x0:x1] = np.clip(
				crop_bgr.astype(np.float32) * (1.0 - mc_weight) + out_crop_bgr.astype(np.float32) * mc_weight,
				0,
				255,
			).astype(np.uint8)

		return result

	def _inpaint_scaled_mode(self, img_bgr: np.ndarray, mask: np.ndarray, target_dim: int = 512) -> np.ndarray:
		"""STRATEGY 2: SCALED 512x512 RESOLUTION (BALANCED SPEED FOR LOW-END HARDWARE)."""
		orig_h, orig_w = img_bgr.shape[:2]
		img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

		in_img = cv2.resize(img_rgb, (target_dim, target_dim), interpolation=cv2.INTER_AREA)
		in_mask = cv2.resize((mask > 0).astype(np.uint8) * 255, (target_dim, target_dim), interpolation=cv2.INTER_NEAREST)

		x = (in_img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
		m = (in_mask > 0).astype(np.float32)[None, None, :, :]

		outputs = self._run_inference(x, m)
		out = outputs[0][0]

		if out.ndim == 3 and out.shape[0] in (1, 3, 4):
			out = out.transpose(1, 2, 0)
		if out.max() <= 1.01:
			out = out * 255.0

		out = np.clip(out, 0, 255).astype(np.uint8)
		out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
		out_resized = cv2.resize(out_bgr, (orig_w, orig_h), interpolation=cv2.INTER_CUBIC)

		mask_weight = (mask.astype(np.float32) / 255.0)[:, :, None]
		result = img_bgr.astype(np.float32) * (1.0 - mask_weight) + out_resized.astype(np.float32) * mask_weight
		return np.clip(result, 0, 255).astype(np.uint8)

	def _inpaint_full_mode(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
		"""STRATEGY 1: FULL DYNAMIC CANVAS RESOLUTION."""
		orig_h, orig_w = img_bgr.shape[:2]
		img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
		mask_bin = (mask > 0).astype(np.float32)

		out_bgr = self._inpaint_single_patch(img_rgb, mask_bin)
		mask_weight = (mask.astype(np.float32) / 255.0)[:, :, None]
		result = img_bgr.astype(np.float32) * (1.0 - mask_weight) + out_bgr.astype(np.float32) * mask_weight
		return np.clip(result, 0, 255).astype(np.uint8)

	def __call__(self, img_bgr: np.ndarray, mask: np.ndarray, mode: str = "patch") -> np.ndarray:
		if not np.any(mask):
			return img_bgr

		if self.fixed_size is not None:
			return self._inpaint_scaled_mode(img_bgr, mask, target_dim=self.fixed_size[0])

		strategy = mode.lower().strip() if mode else "patch"
		if strategy == "scaled":
			return self._inpaint_scaled_mode(img_bgr, mask)
		elif strategy == "full":
			return self._inpaint_full_mode(img_bgr, mask)
		else:
			return self._inpaint_patch_mode(img_bgr, mask)

