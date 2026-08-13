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
			mask=self._mask if self._mask is not None else np.zeros((PAGE_H, PAGE_W), dtype=np.uint8),
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
		assert r.category == "dialogue"  # CATEGORY UNCHANGED BY THE GROWTH

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
		assert r.text == "穿越者！！"
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
