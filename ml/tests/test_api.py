# API TESTS — THE HTTP CONTRACT WITH FAKE BACKENDS (NO MODEL WEIGHTS NEEDED).
#
# pipeline.DETECTOR / OCR / INPAINTER ARE MODULE-LEVEL SINGLETONS → MONKEYPATCHED PER TEST.
from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.detect import DetectResult
from app.inpaint import Inpainter
from app.main import app
from app.ocr import OcrResult
from tests.conftest import DIALOGUE_BOX, PAGE_H, PAGE_W, SFX_BOX

client = TestClient(app)


class FakeDetector:
	"""RETURNS THE TWO SYNTHETIC REGIONS (DIALOGUE TOP-LEFT, SFX BOTTOM-RIGHT)."""

	def __init__(self, available: bool = True) -> None:
		self._available = available

	def available(self) -> bool:
		return self._available

	def analyze(self, img_bgr: np.ndarray) -> DetectResult:
		mask = np.zeros((PAGE_H, PAGE_W), dtype=np.uint8)
		mask[150:230, 100:420] = 255
		return DetectResult(boxes=[DIALOGUE_BOX, SFX_BOX], scores=[0.9, 0.85], mask=mask, backend="comic-ctd")


@pytest.fixture(autouse=True)
def fake_backends(monkeypatch):
	monkeypatch.setattr(pipeline, "detector", FakeDetector())
	monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text="系统", score=0.99))
	# THE HYBRID UNION ALWAYS RUNS RAPIDOCR FULL-PAGE DET — EMPTY IN TESTS UNLESS THE TEST SAYS OTHERWISE
	monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [])
	yield


class TestHealth:
	def test_reports_backends(self):
		resp = client.get("/health")
		assert resp.status_code == 200
		body = resp.json()
		assert body["status"] == "ok"
		assert body["detector"] == "comic-ctd"
		assert body["inpainter"] in ("lama", "opencv")
		assert body["ocr"] == "rapidocr"


class TestAnalyze:
	def test_returns_regions_in_reading_order(self, page_png):
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		body = resp.json()
		assert body["width"] == PAGE_W
		assert body["height"] == PAGE_H
		assert body["backend"] == "comic-ctd"
		assert [r["id"] for r in body["regions"]] == ["r0", "r1"]
		# READING ORDER: DIALOGUE (TOP) FIRST, SFX (BOTTOM) SECOND
		assert body["regions"][0]["box"] == {"x": 100, "y": 150, "w": 320, "h": 80}
		assert body["regions"][1]["category"] == "sfx"
		assert body["regions"][0]["text"] == "系统"
		assert body["regions"][0]["confidence"] == pytest.approx(0.99)
		assert body["regions"][0]["vertical"] is False

	def test_rapidocr_fallback_backend(self, monkeypatch, page_png):
		monkeypatch.setattr(pipeline, "detector", FakeDetector(available=False))
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [(DIALOGUE_BOX, "系统", 0.95), (SFX_BOX, "轰", 0.9)],
		)
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		assert resp.json()["backend"] == "rapidocr-fallback"
		assert len(resp.json()["regions"]) == 2

	def test_empty_page_yields_no_regions(self, monkeypatch, page_png):
		class EmptyDetector(FakeDetector):
			def analyze(self, img_bgr):
				return DetectResult(boxes=[], scores=[], mask=None)

		monkeypatch.setattr(pipeline, "detector", EmptyDetector())
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		assert resp.json()["regions"] == []

	def test_hybrid_union_merges_same_line_and_recovers_missed_text(self, monkeypatch, page_png):
		# THE REAL-WORLD REGRESSION: THE COMIC DETECTOR SPLIT ONE LINE AND MISSED ITS LEFT HALF.
		# RAPIDOCR'S FULL-PAGE DET SEES THE WHOLE LINE — THE UNION MUST TAKE THE WIDER BOX.
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [
				(
					np.array([[60, 148], [440, 148], [440, 232], [60, 232]], dtype=np.float64),
					"你好，世界！",
					0.99,
				),
			],
		)
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		body = resp.json()
		regions = body["regions"]
		# THE DIALOGUE LINE (COMIC 100..420 ∪ RAPID 60..440) BECAME ONE REGION — THE SFX STAYS SEPARATE
		assert len(regions) == 2
		r0 = regions[0]
		assert r0["box"] == {"x": 60, "y": 148, "w": 380, "h": 84}  # THE UNION OF BOTH BOXES
		assert r0["text"] == "你好，世界！"  # THE RAPIDOCR LINE'S OWN TEXT WINS (FULL-LINE CONTEXT)
		assert regions[1]["category"] == "sfx"

	def test_multi_line_bubble_becomes_one_paragraph_region(self, monkeypatch, page_png):
		# THE USER-REPORTED CASE: A BUBBLE WITH TWO STACKED LINES MUST BE ONE REGION (ONE
		# TRANSLATION + ONE TYPESET BLOCK), NOT TWO SCATTERED LINES.
		line1 = np.array([[150, 300], [450, 300], [450, 350], [150, 350]], dtype=np.float64)  # TOP
		line2 = np.array([[180, 360], [420, 360], [420, 410], [180, 410]], dtype=np.float64)  # BELOW
		class ParagraphDetector(FakeDetector):
			def analyze(self, img_bgr):
				return DetectResult(boxes=[line1, line2, SFX_BOX], scores=[0.8, 0.7, 0.85], mask=None)

		monkeypatch.setattr(pipeline, "detector", ParagraphDetector())
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [
				(line1, "这是第一行", 0.99),
				(line2, "这是第二行", 0.98),
			],
		)
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		regions = resp.json()["regions"]
		# TWO REGIONS TOTAL: THE BUBBLE PARAGRAPH + THE SFX
		assert len(regions) == 2
		r0 = regions[0]
		assert r0["box"] == {"x": 150, "y": 300, "w": 300, "h": 110}  # THE UNION OF BOTH LINES
		assert r0["text"] == "这是第一行\n这是第二行"  # JOINED IN READING ORDER
		assert regions[1]["category"] == "sfx"

	def test_garbage_upload_is_400(self):
		resp = client.post("/pages/analyze", files={"image": ("bad.bin", b"not an image", "application/octet-stream")})
		assert resp.status_code == 400

	def test_avif_upload_is_decoded(self):
		# REGRESSION: OPENCV 5.x WHEELS CANNOT DECODE AVIF (cv2.imdecode → None) — decode_image MUST
		# FALL BACK TO PILLOW. THE FIXTURE IS ENCODED WITH PILLOW (NATIVE AVIF SUPPORT).
		from io import BytesIO

		from PIL import Image

		buf = BytesIO()
		Image.fromarray(np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)).save(buf, format="AVIF")
		resp = client.post("/pages/analyze", files={"image": ("page.avif", buf.getvalue(), "image/avif")})
		assert resp.status_code == 200
		assert resp.json()["regions"][0]["box"] == {"x": 100, "y": 150, "w": 320, "h": 80}

	def test_empty_upload_is_400(self):
		resp = client.post("/pages/analyze", files={"image": ("empty.png", b"", "image/png")})
		assert resp.status_code == 400

	def test_missing_file_is_422(self):
		resp = client.post("/pages/analyze")
		assert resp.status_code == 422


class TestClean:
	def test_erases_regions_and_returns_png(self, monkeypatch, page_png):
		# FORCE THE OPENCV BACKEND SO THE TEST IS DETERMINISTIC (NO TORCH DOWNLOAD AT RUNTIME)
		monkeypatch.setattr(pipeline, "get_inpainter", lambda: Inpainter(backend="opencv"))
		payload = [
			{"id": "r0", "box": {"x": 100, "y": 150, "w": 320, "h": 80}},
			{"id": "r1", "box": {"x": 50, "y": 900, "w": 700, "h": 100}, "polygon": [[50, 900], [750, 900], [750, 1000], [50, 1000]]},
		]
		resp = client.post(
			"/pages/clean",
			files={"image": ("page.png", page_png, "image/png")},
			data={"regions": json.dumps(payload)},
		)
		assert resp.status_code == 200
		assert resp.headers["content-type"] == "image/png"
		assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

	def test_empty_regions_returns_unchanged_image(self, monkeypatch, page_png):
		monkeypatch.setattr(pipeline, "get_inpainter", lambda: Inpainter(backend="opencv"))
		resp = client.post("/pages/clean", files={"image": ("page.png", page_png, "image/png")}, data={"regions": "[]"})
		assert resp.status_code == 200
		assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

	def test_invalid_regions_json_is_400(self, page_png):
		resp = client.post("/pages/clean", files={"image": ("page.png", page_png, "image/png")}, data={"regions": "not json"})
		assert resp.status_code == 400
