# DIAGNOSTIC — WHICH CHANNEL ORDER DOES THE ONNX EXPORT ACTUALLY EXPECT? (RUN-ONCE, NOT A TEST)
#
# UPSTREAM'S preprocess_img DOES: BGR→RGB, LETTERBOX, transpose((2,0,1))[::-1] — THE NET EFFECT IS
# *BGR* INPUT DESPITE THE COMMENT SAYING "BGR to RGB". WE TEST ALL FOUR ORDERS ON A PAGE WITH REAL
# (PIL-RENDERED) CJK TEXT AND PICK THE ONE WITH DETECTIONS.
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.detect import ComicTextDetector, crop_padded, letterbox, lines_map_to_boxes  # noqa: E402


def make_text_page() -> np.ndarray:
	from PIL import Image, ImageDraw, ImageFont

	img = Image.new("RGB", (800, 1000), "white")
	d = ImageDraw.Draw(img)
	font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 48)
	d.text((100, 100), "你好，世界！", fill="black", font=font)
	d.text((100, 300), "系统提示：开始翻译", fill="black", font=font)
	d.text((500, 700), "轰！", fill="black", font=font)
	return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def try_order(img_bgr: np.ndarray, mode: str) -> dict:
	# mode in {rgb, bgr} — build the tensor with the given NET CHANNEL ORDER
	if mode == "rgb":
		img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
		img_in, _r, (dw, dh) = letterbox(img_rgb, new_shape=(1024, 1024), auto=False, stride=64)
		tensor = img_in.transpose((2, 0, 1))[None].astype(np.float32) / 255.0
	else:
		img_in, _r, (dw, dh) = letterbox(img_bgr, new_shape=(1024, 1024), auto=False, stride=64)
		tensor = img_in.transpose((2, 0, 1))[::-1][None].astype(np.float32) / 255.0

	det = ComicTextDetector()
	det._load()
	out = det._session.run(None, {det._session.get_inputs()[0].name: tensor})
	mask = np.squeeze(out[1])
	lines = np.squeeze(out[2])
	if lines.ndim == 3 and lines.shape[0] > 1:
		lines = lines[0]
	mask = crop_padded(mask, dw, dh)
	lines = crop_padded(lines, dw, dh)
	boxes, scores = lines_map_to_boxes(lines, dest_width=img_bgr.shape[1], dest_height=img_bgr.shape[0])
	return {
		"mode": mode,
		"mask_mean": float(mask.mean()),
		"lines_max": float(lines.max()),
		"lines_over_03": float((lines > 0.3).mean()),
		"boxes": len(boxes),
		"scores": [round(s, 3) for s in scores[:5]],
	}


def main() -> int:
	page = make_text_page()
	cv2.imwrite("verify-out/diag-text-page.png", page)
	for mode in ("rgb", "bgr"):
		try:
			print(try_order(page, mode))
		except Exception as e:
			print(mode, "ERROR", e)
	return 0


if __name__ == "__main__":
	main()
