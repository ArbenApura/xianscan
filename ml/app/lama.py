# LAMA INPAINTING — DIRECT TORCHSCRIPT INFERENCE (APACHE-2.0).
#
# big-lama.pt IS A TorchScript JIT EXPORT OF THE LaMa UNET (FROM Sanster/models, THE SAME WEIGHTS
# iopaint USES — iopaint ITSELF IS ARCHIVED AND UNINSTALLABLE ON PYTHON 3.14, SO WE RUN THE MODEL
# DIRECTLY; THE CONTRACT BELOW IS EXACTLY iopaint/model/lama.py's forward()).
#
#   INPUT:  img_bgr (H, W, 3) uint8 BGR, mask (H, W) uint8 (255 = ERASE)
#   OUTPUT: img_bgr WITH THE MASKED REGIONS REBUILT
#
# THE MODEL REQUIRES DIMS DIVISIBLE BY 8 (pad_mod=8) AND OUTPUTS RGB FLOATS IN [0, 1].
from __future__ import annotations

import cv2
import numpy as np
import torch

PAD_MOD = 8


class LamaInpainter:
	def __init__(self, model_path: str) -> None:
		self.model = torch.jit.load(model_path, map_location="cpu").eval()

	def __call__(self, img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
		h, w = img_bgr.shape[:2]
		pad_b = (PAD_MOD - h % PAD_MOD) % PAD_MOD
		pad_r = (PAD_MOD - w % PAD_MOD) % PAD_MOD

		img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
		mask_bin = (mask > 0).astype(np.float32)
		if pad_b or pad_r:
			img_rgb = cv2.copyMakeBorder(img_rgb, 0, pad_b, 0, pad_r, cv2.BORDER_REPLICATE)
			mask_bin = cv2.copyMakeBorder(mask_bin, 0, pad_b, 0, pad_r, cv2.BORDER_CONSTANT, value=0)

		# HWC → CHW, /255 → [0, 1]; MASK → (1, 1, H, W)
		x = torch.from_numpy(img_rgb.transpose(2, 0, 1).astype(np.float32) / 255.0).unsqueeze(0)
		m = torch.from_numpy(mask_bin).unsqueeze(0).unsqueeze(0)

		with torch.no_grad():
			out = self.model(x, m)[0].permute(1, 2, 0).numpy()

		out = np.clip(out * 255, 0, 255).astype(np.uint8)
		out = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
		if pad_b or pad_r:
			out = out[:h, :w]
		return out
