#!/usr/bin/env python3
"""REAL-MODEL SMOKE TEST — RUNS THE ACTUAL DETECTOR + OCR + INPAINTER ON A TEXT-RENDERED PAGE AND
WRITES ARTIFACTS TO ./verify-out/ FOR HUMAN INSPECTION. NOT PART OF `pytest` (NEEDS THE ~300MB
MODELS): RUN AFTER `python scripts/download_models.py`.

    python scripts/verify_models.py [--out ./verify-out]

ARTIFACTS:
    analyze.json     — THE /pages/analyze RESPONSE (REGIONS + OCR TEXT)
    regions.png      — PAGE WITH DETECTED REGIONS + OCR TEXT OVERLAID
    cleaned.png      — THE /pages/clean OUTPUT (ORIGINAL TEXT ERASED)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# WINDOWS CONSOLES DEFAULT TO cp1252 — CJK TEXT FROM OCR WOULD CRASH THE PRINTS
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.main import app  # noqa: E402
from tests.conftest import make_text_page  # noqa: E402


def draw_overlay(img_bgr: np.ndarray, regions: list) -> np.ndarray:
	out = img_bgr.copy()
	for r in regions:
		pts = np.array(r["polygon"], dtype=np.int32).reshape(-1, 1, 2)
		cv2.polylines(out, [pts], True, (0, 0, 255), 3)
		label = f"{r['id']}: {r['text']}"
		x, y = r["box"]["x"], max(0, r["box"]["y"] - 8)
		cv2.putText(out, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
	return out


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "verify-out")
	args = parser.parse_args()
	args.out.mkdir(parents=True, exist_ok=True)

	from app.inpaint import get_inpainter

	print(f"backend availability: detector={pipeline.detector.available()} inpainter={get_inpainter().backend}")

	page = make_text_page()
	print(f"page: {page.shape[1]}x{page.shape[0]}")

	result = pipeline.analyze_image(page)
	print(f"analyze backend={result.backend} regions={len(result.regions)}")
	for r in result.regions:
		print(f"  {r.id}: box={r.box} text={r.text!r} conf={r.confidence:.2f}")

	(args.out / "analyze.json").write_text(json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
	overlay = draw_overlay(page, [r.model_dump() for r in result.regions])
	cv2.imwrite(str(args.out / "regions.png"), overlay)

	# THE /pages/clean ROUND-TRIP VIA THE REAL HTTP STACK
	from fastapi.testclient import TestClient

	client = TestClient(app)
	payload = [{"id": r.id, "box": r.box.model_dump(), "polygon": r.polygon} for r in result.regions]
	ok, buf = cv2.imencode(".png", page)
	assert ok
	resp = client.post(
		"/pages/clean",
		files={"image": ("page.png", buf.tobytes(), "image/png")},
		data={"regions": json.dumps(payload)},
	)
	if resp.status_code != 200:
		print(f"clean FAILED: {resp.status_code} {resp.text}")
		return 1
	(args.out / "cleaned.png").write_bytes(resp.content)
	print(f"artifacts written to {args.out}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
