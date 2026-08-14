#!/usr/bin/env python3
"""Download the ML models the sidecar needs into ./models (gitignored).

Manifest (all Apache-2.0 or permissive):
- comictextdetector.pt.onnx  — comic text detector ONNX export (manga-image-translator, Apache-2.0).
  SHA-256 is pinned in manga-image-translator/detection/ctd.py for the 'model-cpu' variant.
- lama.onnx                  — LaMa inpainting ONNX weights (Carve/LaMa-ONNX, Apache-2.0).
  Standard resolution-robust LaMa UNet ONNX model for onnxruntime inference.
- RapidOCR v3 models         — auto-downloaded by the rapidocr package on first run (Apache-2.0);
  this script pre-fetches them too so the first analyze call never stalls.

Usage:  python scripts/download_models.py [--models-dir ./models] [--skip-lama]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

MANIFEST: list[dict] = [
	{
		"name": "comictextdetector.pt.onnx",
		"url": "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/comictextdetector.pt.onnx",
		"sha256": "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f",
		"size": 94_669_756,
	},
	{
		"name": "lama.onnx",
		"url": "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
		"sha256": "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6",
		"size": 208_044_816,
	},
]


def _digest(path: Path, algo: str) -> str:
	h = hashlib.new(algo)
	with path.open("rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def _verify(entry: dict, dest: Path) -> bool:
	"""TRUE WHEN THE FILE MATCHES THE MANIFEST (SIZE + SHA-256 WHEN PINNED, ELSE MD5 WHEN PINNED)."""
	if dest.stat().st_size != entry["size"]:
		return False
	if entry.get("sha256") is not None:
		return _digest(dest, "sha256") == entry["sha256"]
	if entry.get("md5") is not None:
		return _digest(dest, "md5") == entry["md5"]
	return True


def _download(url: str, dest: Path) -> None:
	print(f"  downloading {url}")
	req = urllib.request.Request(url, headers={"User-Agent": "xianscan/0.1"})
	with urllib.request.urlopen(req) as resp, dest.open("wb") as out:
		total = int(resp.headers.get("Content-Length") or 0)
		done = 0
		while True:
			chunk = resp.read(1 << 20)
			if not chunk:
				break
			out.write(chunk)
			done += len(chunk)
			if total:
				pct = done * 100 // total
				print(f"    {pct:3d}%  {done / 1e6:7.1f}/{total / 1e6:7.1f} MB", end="\r")
	print()


def main() -> int:
	parser = argparse.ArgumentParser(description="Download xianscan ML models.")
	parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
	parser.add_argument("--skip-lama", "--skip-big-lama", dest="skip_lama", action="store_true", help="skip the inpainting weights")
	args = parser.parse_args()

	args.models_dir.mkdir(parents=True, exist_ok=True)
	ok = True
	for entry in MANIFEST:
		if args.skip_lama and entry["name"] == "lama.onnx":
			print(f"[skip] {entry['name']} (--skip-lama)")
			continue
		dest = args.models_dir / entry["name"]
		if dest.exists() and _verify(entry, dest):
			print(f"[ok]   {entry['name']} already present")
			continue
		print(f"[get]  {entry['name']} ({entry['size'] / 1e6:.0f} MB)")
		try:
			_download(entry["url"], dest)
		except Exception as e:  # NETWORK ERRORS SHOULD NOT ABORT THE WHOLE RUN
			print(f"[FAIL] {entry['name']}: {e}")
			ok = False
			continue
		if not _verify(entry, dest):
			print(f"[FAIL] {entry['name']}: checksum mismatch")
			dest.unlink(missing_ok=True)
			ok = False
			continue
		print(f"[ok]   {entry['name']} verified")

	# PRE-FETCH THE RAPIDOCR MODELS SO THE FIRST /pages/analyze CALL NEVER STALLS ON A DOWNLOAD.
	print("[get]  rapidocr models (first-run download)")
	try:
		from rapidocr import RapidOCR

		RapidOCR()
		print("[ok]   rapidocr models ready")
	except Exception as e:
		print(f"[warn] rapidocr model pre-fetch failed: {e}")

	if not ok:
		print("Some downloads failed — re-run this script to resume.")
		return 1
	print("All models ready.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
