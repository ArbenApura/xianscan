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
		self.session = ort.InferenceSession(
			model_path,
			sess_options=opts,
			providers=config.ORT_PROVIDERS,
		)
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

	def __call__(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
		if not np.any(mask):
			return img_bgr

		orig_h, orig_w = img_bgr.shape[:2]
		img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

		if self.fixed_size is not None:
			fw, fh = self.fixed_size
			in_img = cv2.resize(img_rgb, (fw, fh), interpolation=cv2.INTER_AREA)
			in_mask = cv2.resize((mask > 0).astype(np.uint8) * 255, (fw, fh), interpolation=cv2.INTER_NEAREST)

			x = (in_img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
			m = (in_mask > 0).astype(np.float32)[None, None, :, :]

			outputs = self.session.run(None, {self.img_name: x, self.mask_name: m})
			out = outputs[0][0]

			if out.ndim == 3 and out.shape[0] in (1, 3, 4):
				out = out.transpose(1, 2, 0)
			if out.max() <= 1.01:
				out = out * 255.0

			out = np.clip(out, 0, 255).astype(np.uint8)
			out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
			out_resized = cv2.resize(out_bgr, (orig_w, orig_h), interpolation=cv2.INTER_CUBIC)

			# COMPOSITE: ONLY REPLACE THE MASKED REGIONS SO UNMASKED ARTWORK STAYS 100% PRISTINE
			mask_weight = (mask.astype(np.float32) / 255.0)[:, :, None]
			result = (img_bgr.astype(np.float32) * (1.0 - mask_weight) + out_resized.astype(np.float32) * mask_weight)
			return np.clip(result, 0, 255).astype(np.uint8)

		# DYNAMIC RESOLUTION PATH
		pad_b = (PAD_MOD - orig_h % PAD_MOD) % PAD_MOD
		pad_r = (PAD_MOD - orig_w % PAD_MOD) % PAD_MOD

		mask_bin = (mask > 0).astype(np.float32)
		if pad_b or pad_r:
			img_rgb = cv2.copyMakeBorder(img_rgb, 0, pad_b, 0, pad_r, cv2.BORDER_REPLICATE)
			mask_bin = cv2.copyMakeBorder(mask_bin, 0, pad_b, 0, pad_r, cv2.BORDER_CONSTANT, value=0)

		x = (img_rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
		m = mask_bin[None, None, :, :]

		outputs = self.session.run(None, {self.img_name: x, self.mask_name: m})
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
