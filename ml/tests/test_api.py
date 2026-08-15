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
		assert body["inpainter"] in ("lama-onnx", "unsupported")
		assert body["ocr"] == "rapidocr"

	def test_does_not_load_heavy_inpainter(self, monkeypatch):
		# /health MUST STAY CHEAP — LOADING LaMa (~200MB TorchScript) BELONGS TO THE FIRST /pages/clean.
		from app import inpaint

		def boom():
			raise AssertionError("health must not load the inpainter model")

		monkeypatch.setattr(inpaint, "_get_lama", boom)
		resp = client.get("/health")
		assert resp.status_code == 200


class TestAnalyze:
	def test_returns_regions_in_reading_order(self, page_png):
		resp = client.post("/pages/analyze", files={"image": ("page.png", page_png, "image/png")})
		assert resp.status_code == 200
		body = resp.json()
		assert body["width"] == PAGE_W
		assert body["height"] == PAGE_H
		assert body["backend"] == "comic-ctd"
		assert [r["id"] for r in body["regions"]] == ["r0", "r1"]
		# READING ORDER: FIRST REGION (TOP), SECOND REGION (BOTTOM)
		assert body["regions"][0]["box"] == {"x": 100, "y": 150, "w": 320, "h": 80}
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


class BoxDetector:
	"""RETURNS ARBITRARY BOXES (+ OPTIONAL TEXT-PROBABILITY MASK) — USED TO REPRODUCE DETECTOR
	GEOMETRY AROUND OCR LINES."""

	def __init__(self, boxes: list[np.ndarray], mask: np.ndarray | None = None) -> None:
		self._boxes = boxes
		self._mask = mask

	def available(self) -> bool:
		return True

	def analyze(self, img_bgr: np.ndarray) -> DetectResult:
		return DetectResult(
			boxes=list(self._boxes),
			scores=[0.9] * len(self._boxes),
			mask=self._mask if self._mask is not None else np.zeros((img_bgr.shape[0], img_bgr.shape[1]), dtype=np.uint8),
			backend="comic-ctd",
		)


def _box(x: int, y: int, w: int, h: int) -> np.ndarray:
	return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)


class TestEllipsisRecovery:
	"""THE REC MODEL OFTEN READS ONLY PART OF A DOTTED LINE ("......" → "..."). THE INPAINT
	POLYGON MUST STILL COVER THE WHOLE LINE, OR THE REMAINING DOTS STAY ON THE CLEANED PAGE."""

	def test_wide_union_box_recovers_trailing_dots(self, monkeypatch):
		# COMIC DETECTOR SEES THE WHOLE 6-DOT LINE (WIDE BOX); RapidOCR ONLY THE FIRST "..." —
		# THE TIGHT OCR HULL WOULD ERASE JUST 3 DOTS. THE POLYGON MUST EXTEND TO THE UNION BOX,
		# AND THE TYPESET BOX MUST FOLLOW IT (OTHERWISE THE TEXT RENDERS OFF-CENTER).
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 320, 40)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(120, 160, 60, 25), "...", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text == "......"  # THE EXTRACTED TEXT REFLECTS THE DOTS THE MASK NOW COVERS
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].min()) <= 100  # EXTENDED LEFT TO THE UNION BOX
		assert int(poly[:, 0].max()) >= 420  # EXTENDED RIGHT — THE MISSING DOTS ARE COVERED
		assert int(poly[:, 1].min()) >= 160  # Y-BAND STAYS TIGHT (NO ART ABOVE)
		assert int(poly[:, 1].max()) <= 185  # ... OR BELOW
		# THE TYPESET BOX MATCHES THE WIDENED MASK — CENTERED OVER THE FULL DOTTED LINE
		assert r.box.x <= 100
		assert r.box.x + r.box.w >= 420
		assert r.box.y >= 160
		assert r.box.y + r.box.h <= 185

	def test_narrow_boxes_grow_rightward_when_text_is_all_dots(self, monkeypatch):
		# EVERY DETECTOR SAW ONLY THE FIRST 3 DOTS — THE POLYGON GROWS RIGHTWARD (CLAMPED TO
		# THE PAGE) TO REACH THE REST OF THE LINE, AND THE TYPESET BOX FOLLOWS.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 60, 40)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(105, 160, 50, 25), "...", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		r = res.regions[0]
		assert r.text == "......"  # TEXT FOLLOWS THE GROWN MASK
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) > 160  # GROWN PAST THE DETECTED DOTS
		assert int(poly[:, 0].max()) <= PAGE_W  # CLAMPED TO THE PAGE EDGE
		assert r.box.x + r.box.w > 160  # TYPESET BOX FOLLOWS THE GROWN MASK

	def test_plain_text_keeps_the_tight_hull(self, monkeypatch):
		# NON-ELLIPSIS TEXT MUST KEEP THE TIGHT HULL (d12f433 BEHAVIOUR — NO OVER-ERASE OF ART).
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 320, 40)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(120, 160, 80, 25), "你好", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		r = res.regions[0]
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) <= 205  # HULL-TIGHT — NOT THE WIDE UNION BOX
		assert r.box.x + r.box.w <= 205  # THE TYPESET BOX STAYS HULL-TIGHT TOO

	def test_mask_guided_growth_catches_an_undetected_dots_line(self, monkeypatch):
		# THE REAL FAILURE: "......" ON ITS OWN LINE AT THE BOTTOM OF A BUBBLE PRODUCES NO BOX
		# AT ALL — BUT THE DETECTOR'S TEXT-PROBABILITY MASK STILL HAS SIGNAL THERE. THE REGION
		# MASK (AND THE TYPESET BOX) MUST GROW DOWNWARD TO COVER THOSE DOTS.
		mask = np.zeros((PAGE_H, PAGE_W), dtype=np.uint8)
		mask[212:227, 100:260] = 255  # THE MISSED DOTS LINE JUST BELOW THE LAST DIALOGUE LINE
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 200, 60)], mask=mask))
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [(_box(110, 158, 180, 22), "大姐大，", 0.9), (_box(105, 184, 190, 22), "轻，轻点", 0.9)],
		)

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text == "大姐大，\n轻，轻点\n……"  # THE GROWN DOTS LINE JOINS THE EXTRACTED TEXT
		poly = np.asarray(r.polygon)
		assert int(poly[:, 1].max()) >= 226  # GROWN DOWN TO COVER THE DOTS LINE
		assert int(poly[:, 0].max()) >= 260  # AND ITS FULL WIDTH
		assert r.box.y + r.box.h >= 226  # TYPESET BOX FOLLOWS — TEXT STAYS CENTERED

	def test_lone_dot_line_joins_the_last_word(self, monkeypatch):
		# THE USER-REPORTED PATTERN: "Transmigration.." WITH A LONE "." ON ITS OWN LINE IN THE
		# SAME REGION — THE DOT MUST JOIN THE LAST WORD, NOT STAY A SEPARATE LINE.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 220, 64)]))
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [(_box(110, 158, 180, 24), "Transmigration..", 0.9), (_box(115, 192, 40, 16), ".", 0.7)],
		)

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		assert res.regions[0].text == "Transmigration..."

	def test_lone_dot_region_merges_into_the_region_above(self, monkeypatch):
		# "JINGZHOU" + A SEPARATE "." REGION RIGHT BELOW → ONE REGION "JINGZHOU." COVERING BOTH.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 100, 200, 40), _box(120, 148, 30, 16)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(105, 106, 190, 28), "JINGZHOU", 0.9)])
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text=".", score=0.6))

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text == "JINGZHOU."
		poly = np.asarray(r.polygon)
		assert int(poly[:, 1].max()) >= 164  # THE MERGED MASK COVERS THE DOT TOO

	def test_far_below_lone_dot_still_merges_into_the_text(self, monkeypatch):
		# THE USER-REPORTED CASE: THE "." SITS SEVERAL LINE HEIGHTS BELOW "JINGZHOU" (BUBBLE
		# WHITESPACE) — IT MUST STILL JOIN THE LAST WORD (GAP ALLOWANCE = 6× TEXT HEIGHT).
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 100, 200, 40), _box(120, 300, 20, 16)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(105, 106, 190, 28), "JINGZHOU", 0.9)])
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text=".", score=0.6))

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		assert res.regions[0].text == "JINGZHOU."
		poly = np.asarray(res.regions[0].polygon)
		assert int(poly[:, 1].max()) >= 316  # COVERS THE DOT FAR BELOW

	def test_punctuation_region_right_of_text_merges_horizontally(self, monkeypatch):
		# "穿越者！" + A SEPARATE "？" REGION A BIT TO THE RIGHT (SAME LINE) → "穿越者！？" WITH
		# ONE MASK COVERING BOTH — THE "？" IS THEREFORE INPAINTED TOGETHER WITH THE TEXT.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 100, 180, 40), _box(260, 108, 36, 26)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(110, 106, 160, 28), "穿越者！", 0.9)])
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text="？", score=0.7))

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text in ("穿越者！？", "穿越者！！")
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) >= 296  # COVERS THE "？" TOO

	def test_lone_punctuation_region_mask_is_padded(self, monkeypatch):
		# A STANDALONE "？" REGION (NOTHING TO MERGE INTO): ITS DETECTOR BOX OFTEN CLIPS THE
		# GLYPH — THE MASK MUST BE PADDED SO THE WHOLE GLYPH IS INPAINTED. NO TEXT FABRICATION.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(200, 200, 32, 40)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text="？", score=0.7))

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text == "？"  # UNCHANGED — NO FABRICATED "？！"
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) >= 232 + 12  # PADDED RIGHT (~0.35× HEIGHT)
		assert int(poly[:, 0].min()) <= 200 - 3  # PADDED LEFT (~0.1× HEIGHT)

	def test_far_away_lone_dot_stays_separate(self, monkeypatch):
		# A DOT REGION FAR BELOW THE TEXT BELONGS ELSEWHERE — IT MUST NOT BE SWALLOWED.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 100, 200, 40), _box(120, 400, 30, 16)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(105, 106, 190, 28), "JINGZHOU", 0.9)])
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda img: OcrResult(text=".", score=0.6))

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 2  # THE DOT REGION SURVIVES ON ITS OWN

	def test_wide_union_box_recovers_missing_punctuation(self, monkeypatch):
		# "穿越者！！" READ AS "穿越者！" — THE COMIC DETECTOR SAW THE FULL EXTENT (WIDE BOX).
		# THE MASK MUST EXTEND TO THE UNION BOX AND THE TEXT MUST GET THE "！" BACK.
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 240, 44)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(110, 158, 170, 30), "穿越者！", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		assert len(res.regions) == 1
		r = res.regions[0]
		assert r.text == "穿越者！！"  # THE MATCHING REPEATED PUNCTUATION JOINS THE TEXT
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) >= 340  # COVERS THE WHOLE UNION BOX
		assert r.box.x + r.box.w >= 340  # TYPESET BOX FOLLOWS

	def test_far_missing_punctuation_recovered_by_mask_growth(self, monkeypatch):
		# THE "！" SITS A BIT FAR AND NO BOX SAW IT — BUT THE DETECTOR'S PROBABILITY MASK HAS
		# SIGNAL THERE. MASK-GUIDED GROWTH EXTENDS THE REGION AND THE TEXT GETS THE "！" BACK.
		mask = np.zeros((PAGE_H, PAGE_W), dtype=np.uint8)
		mask[158:188, 285:315] = 255  # THE MISSED "！" RIGHT OF THE TEXT
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 180, 44)], mask=mask))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(110, 158, 160, 30), "穿越者！", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		r = res.regions[0]
		assert r.text == "穿越者！！"  # THE MATCHING REPEATED PUNCTUATION JOINS THE TEXT
		poly = np.asarray(r.polygon)
		assert int(poly[:, 0].max()) >= 314  # GREW TO THE MASKED GLYPH
		assert int(poly[:, 0].max()) <= PAGE_W  # CLAMPED TO THE PAGE EDGE

	def test_padding_alone_does_not_fabricate_a_question_mark(self, monkeypatch):
		# A MERE 8PX OF UNION-BOX PADDING BEYOND THE HULL MUST NOT APPEND A "？" TO THE TEXT
		# (THE GEOMETRY THRESHOLD IS 0.35× LINE HEIGHT = 10.5PX HERE).
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(100, 150, 178, 44)]))
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [(_box(110, 158, 160, 30), "穿越者！", 0.9)])

		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))

		r = res.regions[0]
		assert r.text == "穿越者！"  # TEXT UNCHANGED — ONLY PADDING, NO REAL EXTRA GLYPH


class TestClean:
	def test_erases_regions_and_returns_png(self, monkeypatch, page_png):
		class MockInpainter:
			backend = "lama-onnx"
			def available(self): return True
			def __call__(self, img, mask): return img.copy()

		monkeypatch.setattr(pipeline, "get_inpainter", lambda: MockInpainter())
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
		class MockInpainter:
			backend = "lama-onnx"
			def available(self): return True
			def __call__(self, img, mask): return img.copy()

		monkeypatch.setattr(pipeline, "get_inpainter", lambda: MockInpainter())
		resp = client.post("/pages/clean", files={"image": ("page.png", page_png, "image/png")}, data={"regions": "[]"})
		assert resp.status_code == 200
		assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

	def test_unsupported_inpainter_returns_503(self, monkeypatch, page_png):
		monkeypatch.setattr(pipeline, "get_inpainter", lambda: Inpainter(backend="unsupported"))
		payload = [{"id": "r0", "box": {"x": 100, "y": 150, "w": 320, "h": 80}}]
		resp = client.post(
			"/pages/clean",
			files={"image": ("page.png", page_png, "image/png")},
			data={"regions": json.dumps(payload)},
		)
		assert resp.status_code == 503
		assert "Inpainting is not supported" in resp.json()["detail"]

	def test_invalid_regions_json_is_400(self, page_png):
		resp = client.post("/pages/clean", files={"image": ("page.png", page_png, "image/png")}, data={"regions": "not json"})
		assert resp.status_code == 400

	def test_standalone_watermark_region_is_filtered(self, monkeypatch):
		monkeypatch.setattr(pipeline, "detector", BoxDetector([_box(10, 20, 120, 40), _box(100, 150, 200, 60)]))
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [
				(_box(10, 20, 120, 40), "速漫库", 0.99),
				(_box(100, 150, 200, 60), "这是真对话", 0.95),
			],
		)
		res = pipeline.analyze_image(np.zeros((PAGE_H, PAGE_W, 3), dtype=np.uint8))
		# The standalone watermark "速漫库" is filtered; only "这是真对话" survives
		assert len(res.regions) == 1
		assert res.regions[0].text == "这是真对话"

	def test_sample1_tampered_watermark_speech_bubble(self, monkeypatch):
		"""SAMPLE 1: Top speech bubble has 2 lines (咦！居然让你 / 抽到了这个，),
		top-left watermark (速漫库) is filtered, and bottom bubble is preserved.
		"""
		monkeypatch.setattr(
			pipeline,
			"detector",
			BoxDetector([
				_box(13, 215, 170, 61),   # watermark logo (速漫库)
				_box(510, 215, 306, 120),  # top speech bubble (enclosing both lines)
				_box(374, 1468, 411, 108), # bottom bubble
			]),
		)
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [
				(_box(13, 215, 170, 61), "速漫库", 0.99),
				(_box(520, 220, 280, 45), "咦！居然让你", 0.98),
				(_box(515, 275, 290, 50), "抽到了这个，", 0.97),
				(_box(380, 1475, 390, 45), "这是啥？能让我看到", 0.99),
				(_box(380, 1525, 390, 45), "真实世界的小药丸？", 0.99),
			],
		)
		res = pipeline.analyze_image(np.zeros((1900, 900, 3), dtype=np.uint8))
		# Watermark logo "速漫库" is filtered out
		assert len(res.regions) == 2
		# Top bubble groups both lines
		assert "咦！居然让你" in res.regions[0].text
		assert "抽到了这个，" in res.regions[0].text
		# Bottom bubble groups both lines
		assert "这是啥？能让我看到\n真实世界的小药丸？" == res.regions[1].text

	def test_sample2_stat_card_no_duplication_and_full_lines(self, monkeypatch):
		"""SAMPLE 2: Stat card with 【顶级人物十名。】 and (附带一头顶级宠物)
		must not duplicate the pet text, must recover the top bracketed title, and
		must calculate the tilt/rotation angle accurately.
		"""
		stat_box = _box(330, 1020, 360, 150)
		monkeypatch.setattr(pipeline, "detector", BoxDetector([stat_box]))

		# Simulate RapidOCR detecting the bottom line with ~9.2 degree tilt + duplicate
		tilted_line1 = np.array([[345.0, 1050.0], [674.0, 1105.0], [665.0, 1159.0], [336.0, 1104.0]])
		tilted_line2 = np.array([[347.0, 1052.0], [676.0, 1107.0], [667.0, 1161.0], [338.0, 1106.0]])
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [
				(tilted_line1, "(附带一头顶级宠物)", 0.97),
				(tilted_line2, "(附带一头顶级宠物)", 0.96), # near duplicate
			],
		)
		# Crop recognizer sees the full stat card with both lines
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_crop",
			lambda img: OcrResult(text="【顶级人物十名。】\n(附带一头顶级宠物)", score=0.98),
		)

		res = pipeline.analyze_image(np.zeros((1600, 900, 3), dtype=np.uint8))
		assert len(res.regions) == 1
		r = res.regions[0]
		# Must contain both lines without duplicating the second line
		assert r.text == "【顶级人物十名。】\n(附带一头顶级宠物)"
		assert r.text.count("(附带一头顶级宠物)") == 1
		# Must calculate and preserve the tilt rotation angle (~9.2 degrees)
		assert pytest.approx(r.angle, 0.5) == 9.2

	def test_sample3_multi_region_narration_page(self, monkeypatch):
		"""SAMPLE 3: Page 656 with 5 narrative regions across top, middle, and bottom.
		Verifies that all 5 narrative boxes are detected, cleanly grouped, and sorted in reading order.
		"""
		boxes = [
			_box(21, 14, 392, 59),     # Top narration
			_box(60, 737, 252, 164),   # Left monologue
			_box(536, 736, 183, 108),  # Right monologue
			_box(18, 1003, 356, 30),   # Bottom left
			_box(353, 1003, 308, 73),  # Bottom right
		]
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes))

		ocr_lines = [
			(_box(21, 14, 392, 28), "圣祖山脉之外的世界，已经被妖兽所占领，", 0.99),
			(_box(21, 42, 392, 30), "这里的人们已经有数百年不曾与外界有过联系了。", 0.99),
			(_box(60, 737, 252, 26), "谁也不清楚外面的世界是怎", 0.99),
			(_box(60, 765, 252, 26), "样的。传说人类在鼎盛时期有着庞", 0.99),
			(_box(60, 793, 252, 26), "大的帝国，但如今都已灰飞烟灭，", 0.99),
			(_box(60, 821, 252, 26), "不复存在。", 0.99),
			(_box(60, 849, 252, 26), "这座城市由于位置隐秘，才得以", 0.99),
			(_box(60, 877, 252, 24), "从黑暗时代完整保留下来。", 0.99),
			(_box(536, 736, 183, 26), "虽然经常会受到山脉中", 0.99),
			(_box(536, 764, 183, 26), "风雪妖兽的袭击，但这座", 0.99),
			(_box(536, 792, 183, 26), "城池还是在次次毁灭性的", 0.99),
			(_box(536, 820, 183, 24), "战争中不断重建了起来。", 0.99),
			(_box(18, 1003, 356, 30), "那斑驳的城墙，是一座不朽的丰碑！！", 0.98),
			(_box(353, 1003, 308, 73), "而这座代表人类希望的城市，叫做……", 0.99),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1131, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 5
		# Top narration
		assert "圣祖山脉之外的世界" in res.regions[0].text
		# Middle monologues
		assert any("谁也不清楚外面的世界" in r.text for r in res.regions)
		assert any("从黑暗时代完整保留下来" in r.text for r in res.regions)
		assert any("虽然经常会受到山脉中" in r.text for r in res.regions)
		# Bottom narration
		assert any("那斑驳的城墙" in r.text for r in res.regions)
		assert any("叫做……" in r.text for r in res.regions)

	def test_sample4_vertical_bubble_no_erroneous_90_degree_rotation(self, monkeypatch):
		"""SAMPLE 4: Page 657 with vertical speech bubble (91x163) containing 6 horizontal lines.
		Verifies that tall multi-line speech bubbles maintain angle = 0.0 (never rotated 90 degrees).
		"""
		bubble_box = _box(200, 387, 91, 163)
		monkeypatch.setattr(pipeline, "detector", BoxDetector([bubble_box]))

		ocr_lines = [
			(_box(205, 390, 80, 24), "听说这", 0.99),
			(_box(205, 416, 80, 24), "新老师是", 0.99),
			(_box(205, 442, 80, 24), "神圣世家", 0.99),
			(_box(205, 468, 80, 24), "的，还是", 0.99),
			(_box(205, 494, 80, 24), "个白银妖", 0.99),
			(_box(205, 520, 80, 24), "灵师呢！", 0.99),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1131, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 1
		r = res.regions[0]
		assert "听说这" in r.text
		assert "灵师呢！" in r.text
		# Must NOT be rotated 90 degrees
		assert r.angle == 0.0
		assert r.vertical is True

	def test_sample5_multi_panel_page_drops_giant_detector_blob(self, monkeypatch):
		"""SAMPLE 5: Page 664 with a giant failed detector blob (511x818) spanning across 3 panels.
		Verifies that the giant blob is dropped and individual panel texts are preserved as separate regions.
		"""
		giant_blob = _box(161, 29, 511, 818)
		monkeypatch.setattr(pipeline, "detector", BoxDetector([giant_blob]))

		ocr_lines = [
			(_box(200, 35, 300, 25), "一起穿行在荒芜的沙漠，因为彼此的笑容而坚强……", 0.99),
			(_box(570, 245, 200, 25), "然而，幸福是如此短暂……", 0.99),
			(_box(150, 620, 300, 25), "回眸时，已是阴阳永隔……", 0.99),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1131, 800, 3), dtype=np.uint8))
		# Must NOT collapse into 1 giant 818px region! Must preserve separate regions
		assert len(res.regions) == 3
		# Region 0: top panel text
		assert "一起穿行在荒芜的沙漠" in res.regions[0].text
		assert res.regions[0].box.h < 100
		# Region 1: middle-right panel text
		assert "幸福是如此短暂" in res.regions[1].text
		assert res.regions[1].box.h < 100
		# Region 2: bottom panel text
		assert "回眸时，已是阴阳永隔" in res.regions[2].text
		assert res.regions[2].box.h < 100

	def test_sample6_stray_ocr_artifact_ignored(self, monkeypatch):
		"""SAMPLE 6: Page 58378 with 8 legitimate dialogue / intro regions and 1 stray OCR detection
		error ('L' at x=654, y=1933, w=28, h=28, confidence=0.59284).
		Verifies that the stray 1-character OCR noise is skipped/ignored, producing 0 inpaint and 0 translation,
		while all 8 valid regions are preserved.
		"""
		boxes = [
			_box(151, 114, 108, 42),
			_box(456, 242, 231, 111),
			_box(121, 505, 265, 79),
			_box(167, 798, 108, 47),
			_box(523, 1003, 77, 44),
			_box(66, 1234, 326, 74),
			_box(473, 1407, 244, 76),
			_box(9, 1753, 296, 136),
			_box(654, 1933, 28, 28),
		]
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes))

		ocr_lines = [
			(_box(151, 114, 108, 42), "格斗家", 0.99975),
			(_box(456, 242, 231, 111), "高防御力和强力的团队辅助，团战必备。", 0.99999),
			(_box(121, 505, 265, 79), "强控职业，连招打击是该职业特点。", 0.99988),
			(_box(167, 798, 108, 47), "弓箭手", 0.99967),
			(_box(523, 1003, 77, 44), "牧师", 0.99992),
			(_box(66, 1234, 326, 74), "拥有最远攻击距离，取人首级于千里之外。", 0.99993),
			(_box(473, 1407, 244, 76), "唯一的治疗职业，生命力顽强。", 0.99915),
			(_box(9, 1753, 296, 136), "都怪阿发那小子稀里糊涂的，把已经设定好职业和姓名的账号给了我！", 0.99988),
			(_box(654, 1933, 28, 28), "L", 0.59284),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((2017, 800, 3), dtype=np.uint8))
		# The stray 'L' artifact at (654, 1933) must be filtered out -> exactly 8 regions returned
		assert len(res.regions) == 8
		texts = [r.text for r in res.regions]
		assert "格斗家" in texts[0]
		assert "弓箭手" in texts[3]
		assert "牧师" in texts[4]
		assert not any(r.text.strip() == "L" for r in res.regions)
		assert not any(r.box.x == 654 and r.box.y == 1933 for r in res.regions)

	def test_sample7_page_58383_ellipsis_bounds(self, monkeypatch):
		"""SAMPLE 7: Page 58383 with single-line dialogue ending in ellipsis ('老师……').
		Verifies that the bounding box does not exceed rightward beyond the speech bubble / panel
		into the white page gutter (width stays <= 140px, right edge <= 765px).
		"""
		boxes = [
			_box(623, 172, 130, 44),
			_box(315, 214, 123, 42),
			_box(267, 755, 248, 70),
			_box(76, 846, 91, 42),
		]
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes))

		ocr_lines = [
			(_box(635, 178, 110, 32), "老师……", 0.96947),
			(_box(320, 220, 110, 30), "阿发？!", 0.86711),
			(_box(270, 760, 240, 60), "好了好了，在游\n戏里别叫我老师！", 0.99994),
			(_box(80, 850, 80, 30), "老师?", 0.86114),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1138, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 4
		r0 = next(r for r in res.regions if "老师……" in r.text)
		# Must remain tight to the speech bubble, NOT reaching page margin x=798 / w=175
		assert r0.box.w <= 140, f"Bubble width ({r0.box.w}px) must stay <= 140px, got {r0.box.w}px"
		assert r0.box.x + r0.box.w <= 765, f"Bubble right edge ({r0.box.x + r0.box.w}px) must stay <= 765px"

	def test_sample8_page_45358_hahaha_bottom_bounds(self, monkeypatch):
		"""SAMPLE 8: Page 45358 with dialogue and exclamation '哈哈哈！' ending in terminal punctuation.
		Verifies that '哈哈哈！' bounding box height stays tight to the text (height <= 42px)
		and does not over-detect downward into empty bubble tails or artwork.
		"""
		boxes = [
			_box(76, 71, 135, 153),
			_box(612, 399, 136, 204),
			_box(181, 676, 281, 37),
			_box(212, 800, 256, 143),
			_box(203, 1021, 123, 38),
		]
		mask = np.zeros((1331, 800), dtype=np.uint8)
		# Simulate detector mask that has a bubble tail extending down to y=1080
		mask[1021:1080, 203:326] = 255
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes, mask=mask))

		ocr_lines = [
			(_box(76, 71, 135, 153), "行凶者是\n这个须发\n皆白的老\n头", 0.99996),
			(_box(612, 399, 136, 204), "受害者就\n是我们号\n称会功夫\n的顾飞老\n师。", 0.99992),
			(_box(181, 676, 281, 37), "连校长都这么说——", 0.933),
			(_box(212, 800, 256, 143), "什么是无耻？顾飞\n老师一再说他会功\n夫是我这辈子见过\n最无耻的事。", 0.99993),
			(_box(203, 1021, 123, 35), "哈哈哈！", 0.97298),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1331, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 5
		r_haha = next(r for r in res.regions if "哈哈哈！" in r.text)
		# Must remain tight to text height, NOT stretching down into bubble tail (h <= 42px, not 59px)
		assert r_haha.box.h <= 42, f"'哈哈哈！' box height ({r_haha.box.h}px) must stay <= 42px, got {r_haha.box.h}px"
		assert r_haha.box.y + r_haha.box.h <= 1060, f"'哈哈哈！' bottom ({r_haha.box.y + r_haha.box.h}px) must stay <= 1060px"

	def test_sample9_page_45371_tilted_shout_bubble_detected(self, monkeypatch):
		"""SAMPLE 9: Page 45371 with tilted shout bubble '哇啊啊啊啊！！' in panel 2.
		Verifies that all 5 text elements (sound effects '啪！', '咚！', bottom dialogue, and
		tilted scream balloon '哇啊啊啊啊！！') are detected and included in the regions.
		"""
		boxes = [
			_box(573, 79, 76, 50),
			_box(130, 530, 290, 110),  # Tilted scream bubble '哇啊啊啊啊！！'
			_box(245, 1254, 69, 51),
			_box(605, 1295, 64, 47),
			_box(273, 1609, 256, 123),
		]
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes))

		ocr_lines = [
			(_box(573, 79, 76, 50), "啪！", 0.97008),
			(_box(145, 545, 260, 80), "哇啊啊啊啊！！", 0.965),
			(_box(245, 1254, 69, 51), "咚！", 0.99398),
			(_box(605, 1295, 64, 47), "啪！", 0.91888),
			(_box(273, 1609, 256, 123), "真是个疯子！这\n家伙怎么越打越\n精神？！", 0.99996),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1866, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 5
		texts = [r.text for r in res.regions]
		assert any("哇啊啊啊啊" in t for t in texts), f"Tilted shout bubble must be detected: {texts}"

	def test_sample10_page_45360_split_touching_speech_bubbles(self, monkeypatch):
		"""SAMPLE 10: Page 45360 with two touching circular speech bubbles:
		Bubble A: '靠！反正\\n最多挨顿\\n打，不过\\n是游戏，'
		Bubble B: '真是的，\\n自己又不\\n会受伤'
		Verifies that touching bubbles with shifted centroids and gap are separated into 2 distinct regions,
		and thought-bubble tail circles ('000') are ignored.
		"""
		# Touching speech bubbles enclosed in one detector bounding box
		touching_box = _box(211, 525, 228, 325)
		tail_circles_box = _box(454, 691, 47, 47)
		sfx1_box = _box(333, 1095, 71, 51)
		sfx2_box = _box(498, 1276, 70, 53)

		boxes = [touching_box, tail_circles_box, sfx1_box, sfx2_box]
		monkeypatch.setattr(pipeline, "detector", BoxDetector(boxes))

		ocr_lines = [
			# Bubble A lines (mean center ~295)
			(_box(250, 540, 90, 28), "靠！反正", 0.999),
			(_box(250, 575, 90, 28), "最多挨顿", 0.999),
			(_box(250, 610, 90, 28), "打，不过", 0.999),
			(_box(255, 645, 80, 28), "是游戏，", 0.999),
			# Bubble B lines (mean center ~370, shifted right and down)
			(_box(330, 695, 80, 28), "真是的，", 0.999),
			(_box(330, 730, 80, 28), "自己又不", 0.999),
			(_box(330, 765, 75, 28), "会受伤", 0.999),
			# Thought bubble tail circles
			(_box(454, 691, 47, 47), "000", 0.84732),
			# Sound effects
			(_box(333, 1095, 71, 51), "砰！", 0.98404),
			(_box(498, 1276, 70, 53), "啪！", 0.99564),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1839, 800, 3), dtype=np.uint8))
		# Exactly 4 valid regions (Bubble A, Bubble B, SFX 1, SFX 2) — 000 is filtered
		assert len(res.regions) == 4
		texts = [r.text for r in res.regions]
		# Bubble A and Bubble B must be split
		assert any("靠！反正" in t and "是游戏，" in t for t in texts), f"Bubble A missing: {texts}"
		assert any("真是的，" in t and "会受伤" in t for t in texts), f"Bubble B missing: {texts}"
		# Must NOT be fused into one 7-line block
		assert not any("靠！反正" in t and "真是的，" in t for t in texts), f"Bubbles must not be fused: {texts}"
		# '000' circle noise must be filtered out
		assert not any(r.text.strip() == "000" for r in res.regions)

	def test_sample10_page_45360_needs_rescue_splits_crop_lines(self, monkeypatch):
		"""SAMPLE 10 (Rescue Path): When RapidOCR full-page scan only detects 1 line inside
		the 228x325 box and triggers needs_rescue (recognize_crop), verify that the crop result
		with 7 lines is split into Bubble A and Bubble B instead of assigning the entire 7 lines to one box.
		"""
		touching_box = _box(211, 525, 228, 325)
		monkeypatch.setattr(pipeline, "detector", BoxDetector([touching_box]))

		# RapidOCR full-page only matched 1 line in the box (triggering needs_rescue)
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_full",
			lambda img: [(_box(250, 540, 90, 28), "靠！反正", 0.65)],
		)

		# recognize_crop returns all 7 lines
		crop_ocr_lines = [
			(_box(40, 15, 90, 28), "靠！反正", 0.999),
			(_box(40, 50, 90, 28), "最多挨顿", 0.999),
			(_box(40, 85, 90, 28), "打，不过", 0.999),
			(_box(45, 120, 80, 28), "是游戏，", 0.999),
			(_box(120, 170, 80, 28), "真是的，", 0.999),
			(_box(120, 205, 80, 28), "自己又不", 0.999),
			(_box(120, 240, 75, 28), "会受伤", 0.999),
		]
		crop_text = "\n".join(t for _b, t, _s in crop_ocr_lines)
		crop_res = OcrResult(text=crop_text, score=0.99993, lines=crop_ocr_lines)
		monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda crop: crop_res)

		res = pipeline.analyze_image(np.zeros((1839, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 2
		texts = [r.text for r in res.regions]
		assert any("靠！反正" in t and "是游戏，" in t for t in texts), f"Bubble A missing: {texts}"
		assert any("真是的，" in t and "会受伤" in t for t in texts), f"Bubble B missing: {texts}"
		assert not any("靠！反正" in t and "真是的，" in t for t in texts), f"Bubbles must not be combined: {texts}"

	def test_sample11_page_63532_hollow_circles_ellipsis_normalized(self, monkeypatch):
		"""SAMPLE 11: Page 63532 where hollow outline ellipsis dots '但是......'
		were misrecognized by OCR as '但是0000'.
		Verifies that '但是0000' / '但是oooo' is automatically normalized to '但是……'.
		"""
		box0 = _box(436, 93, 145, 50)
		box1 = _box(87, 903, 272, 141)
		box2 = _box(131, 1317, 326, 103)

		monkeypatch.setattr(pipeline, "detector", BoxDetector([box0, box1, box2]))

		ocr_lines = [
			(_box(436, 93, 145, 50), "但是0000", 0.80501),
			(_box(87, 903, 272, 141), "刚才我可以用功夫\n打败他们，也就是\n说我这个法师有了\n格斗家的“技能”", 0.9999),
			(_box(131, 1317, 326, 103), "如果是格斗家，这职业\n的属性加成一定更适合\n我的功夫发挥。", 0.9999),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		res = pipeline.analyze_image(np.zeros((1560, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 3
		r0 = next(r for r in res.regions if "但是" in r.text)
		assert r0.text == "但是……", f"Expected '但是……', got '{r0.text}'"
		assert "0000" not in r0.text, f"'0000' must not remain in OCR text: '{r0.text}'"

	def test_sample12_page_45403_trailing_line_recovered(self, monkeypatch):
		"""SAMPLE 12: Page 45403 with bottom bubble missing line 3 '一下？'.
		Bubble contains:
		Line 1: '你要不要也'
		Line 2: '来“熟悉”'
		Line 3: '一下？'
		Verifies that the full 3-line dialogue is recovered and not truncated.
		"""
		box0 = _box(305, 81, 278, 137)
		box1 = _box(396, 621, 175, 147)
		box2 = _box(280, 1330, 210, 140)  # Bubble 3 full mask/box

		mask = np.zeros((1598, 800), dtype=np.uint8)
		mask[1330:1470, 280:490] = 255
		monkeypatch.setattr(pipeline, "detector", BoxDetector([box0, box1, box2], mask=mask))

		ocr_lines = [
			(_box(305, 81, 278, 137), "小弟弟，我可是一\n个自律的游戏工作\n者，你别指望从我\n这里得到什么好处。", 0.99991),
			(_box(396, 621, 175, 147), "我没那意思，\n就是想跟着\n你在游戏里\n熟悉一下。", 0.99998),
			# Full page only detected top 2 lines in Bubble 3
			(_box(312, 1348, 169, 32), "你要不要也", 0.9999),
			(_box(312, 1385, 169, 32), "来“熟悉”", 0.9999),
		]
		monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: ocr_lines)

		# recognize_crop or recognize_line on band or crop finds '一下？'
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_crop",
			lambda crop: OcrResult(text="一下？", score=0.98),
		)
		monkeypatch.setattr(
			pipeline.ocr,
			"recognize_line",
			lambda crop: OcrResult(text="一下？", score=0.98),
		)

		res = pipeline.analyze_image(np.zeros((1598, 800, 3), dtype=np.uint8))
		assert len(res.regions) == 3
		r2 = next(r for r in res.regions if "你要不要也" in r.text)
		assert "一下？" in r2.text or "一下?" in r2.text, f"Expected '一下？' in dialogue, got: '{r2.text}'"














