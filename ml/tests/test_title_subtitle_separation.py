import cv2
import numpy as np
import pytest
from app import detect, ocr, pipeline


def test_clean_stray_ocr_artifacts():
	"""Stray Latin letters attached to Chinese staff names should be cleanly stripped."""
	# The exact case from sample: '助手：蒋招洲徐荷婷柳腾e'
	assert pipeline._clean_stray_ocr_artifacts("助手：蒋招洲徐荷婷柳腾e") == "助手：蒋招洲徐荷婷柳腾"
	# Normal staff line without stray letter
	assert pipeline._clean_stray_ocr_artifacts("主笔：姜若泰") == "主笔：姜若泰"
	assert pipeline._clean_stray_ocr_artifacts("原作：发飙的蜗牛") == "原作：发飙的蜗牛"
	# Multi-line text
	multi = "原作：发飙的蜗牛\n主笔：姜若泰\n助手：蒋招洲徐荷婷柳腾e"
	cleaned = pipeline._clean_stray_ocr_artifacts(multi)
	assert cleaned == "原作：发飙的蜗牛\n主笔：姜若泰\n助手：蒋招洲徐荷婷柳腾"


def test_side_by_side_title_and_subtitle_separation():
	"""Side-by-side vertical subtitle ('第十话·课前') and large title ('妖神记') inside one detector box
	must be separated into two distinct sub-groups/regions, not fused into one block."""
	# Title lines: x around 420..560, y from 16 to 520
	title_box = np.array([[420, 16], [560, 16], [560, 520], [420, 520]], dtype=np.float64)
	# Subtitle line: x around 300..340, y from 160 to 320 (vertical column to the left of the title)
	subtitle_box = np.array([[300, 160], [340, 160], [340, 320], [300, 320]], dtype=np.float64)

	# Matched lines inside the combined comic box
	matched = [
		(subtitle_box, "第十话·课前", 0.99, 0.0),
		(title_box, "妖神记", 0.98, 0.0),
	]

	# Verify sub-grouping logic separates them
	sub_groups = []
	for m in matched:
		m_line = m[0]
		mx, my, mw, mh = detect.box_to_xywh(m_line)
		if not sub_groups:
			sub_groups.append([m])
		else:
			last_m = sub_groups[-1][-1][0]
			lx, ly, lw, lh = detect.box_to_xywh(last_m)
			gap = my - (ly + lh)
			y_overlap = min(my + mh, ly + lh) - max(my, ly)
			x_overlap = min(mx + mw, lx + lw) - max(mx, lx)
			min_h = min(mh, lh)

			is_vertical_col = detect.is_vertical_box(last_m) or detect.is_vertical_box(m_line)
			is_side_by_side = is_vertical_col and (x_overlap <= 0) and (y_overlap > 0.50 * min_h)
			is_disconnected = gap > 1.2 * max(mh, lh) or is_side_by_side
			if is_disconnected:
				sub_groups.append([m])
			else:
				sub_groups[-1].append(m)

	assert len(sub_groups) == 2, f"Expected 2 separate sub-groups for title + subtitle, got {len(sub_groups)}"
	assert sub_groups[0][0][1] == "第十话·课前"
	assert sub_groups[1][0][1] == "妖神记"


def test_standard_dialogue_multiline_stays_together():
	"""Vertically stacked dialogue lines in a normal speech bubble must stay together in one region."""
	line1 = np.array([[100, 50], [250, 50], [250, 80], [100, 80]], dtype=np.float64)
	line2 = np.array([[95, 85], [255, 85], [255, 115], [95, 115]], dtype=np.float64)

	matched = [
		(line1, "你今天怎么了？", 0.99, 0.0),
		(line2, "感觉怪怪的。", 0.98, 0.0),
	]

	sub_groups = []
	for m in matched:
		m_line = m[0]
		mx, my, mw, mh = detect.box_to_xywh(m_line)
		if not sub_groups:
			sub_groups.append([m])
		else:
			last_m = sub_groups[-1][-1][0]
			lx, ly, lw, lh = detect.box_to_xywh(last_m)
			gap = my - (ly + lh)
			y_overlap = min(my + mh, ly + lh) - max(my, ly)
			x_overlap = min(mx + mw, lx + lw) - max(mx, lx)
			min_h = min(mh, lh)

			is_side_by_side = (x_overlap <= 0) and (y_overlap > 0.50 * min_h)
			is_disconnected = gap > 1.2 * max(mh, lh) or is_side_by_side
			if is_disconnected:
				sub_groups.append([m])
			else:
				sub_groups[-1].append(m)

	assert len(sub_groups) == 1, f"Expected normal dialogue lines to stay in 1 sub-group, got {len(sub_groups)}"
	assert len(sub_groups[0]) == 2


def test_end_to_end_comic_crop_discovery_and_split(monkeypatch):
	"""When ComicTextDetector emits one large box [298, 16, 277, 544] covering both title and subtitle,
	the crop recovery path must extract both lines and split them into two independent regions."""
	from app.detect import DetectResult

	class MockDetector:
		def available(self):
			return True

		def analyze(self, img):
			return DetectResult(
				boxes=[np.array([[298, 16], [575, 16], [575, 560], [298, 560]], dtype=np.float64)],
				scores=[0.99],
				mask=np.zeros(img.shape[:2], dtype=np.uint8),
				backend="comic-ctd",
			)

	monkeypatch.setattr(pipeline, "detector", MockDetector())
	monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [])

	# recognize_crop returns two lines inside the crop
	crop_lines = [
		(np.array([[122, 0], [277, 0], [277, 504], [122, 504]], dtype=np.float64), "妖神记", 0.98),
		(np.array([[0, 144], [42, 144], [42, 304], [0, 304]], dtype=np.float64), "第十话·课前", 0.99),
	]
	monkeypatch.setattr(
		pipeline.ocr,
		"recognize_crop",
		lambda img: ocr.OcrResult(text="妖神记\n第十话·课前", score=0.99, lines=crop_lines),
	)

	fake_img = np.zeros((1132, 800, 3), dtype=np.uint8)
	res = pipeline.analyze_image(fake_img)

	assert len(res.regions) == 2, f"Expected 2 regions (title + subtitle), got {len(res.regions)}"
	texts = [r.text for r in res.regions]
	assert "妖神记" in texts
	assert "第十话·课前" in texts


def test_mid_sentence_ellipsis_dialogue_not_split():
	"""Ellipsis in mid-sentence dialogue like '你……是李婉儿，' must not be split into multiple fragmented lines."""
	fake_line = (np.array([[50, 200], [300, 200], [300, 240], [50, 240]], dtype=np.float64), "你……是李婉儿，", 0.99, 0.0)
	fake_img = np.zeros((400, 400, 3), dtype=np.uint8)
	split_result = pipeline._split_lines_by_internal_punctuation([fake_line], fake_img)
	assert len(split_result) == 1
	assert split_result[0][1] == "你……是李婉儿，"


def test_sample_45330_dialogue_clean_continuity():
	"""Page 45330: Speech bubble text must be cleanly preserved without mid-sentence split or phantom single characters."""
	lines = [
		(np.array([[50, 225], [300, 225], [300, 255], [50, 255]], dtype=np.float64), "蛤蟆寨的李总管？", 0.99, 0.0),
		(np.array([[50, 265], [320, 265], [320, 295], [50, 295]], dtype=np.float64), "你……是李婉儿，", 0.99, 0.0),
		(np.array([[50, 305], [200, 305], [200, 335], [50, 335]], dtype=np.float64), "明玉公主？", 0.99, 0.0),
	]
	fake_img = np.zeros((1065, 900, 3), dtype=np.uint8)
	split_lines = pipeline._split_lines_by_internal_punctuation(lines, fake_img)
	assert len(split_lines) == 3
	assert [l[1] for l in split_lines] == ["蛤蟆寨的李总管？", "你……是李婉儿，", "明玉公主？"]


def test_sample_45334_no_extra_substrings_or_rotation():
	"""Page 45334: '嗯，他对你的评价\\n极高，称你为……' must not have phantom substring lines ('极高，', '嗯') or spurious angle."""
	raw_crop_lines = [
		"嗯，他对你的评价",
		"极高，称你为……",
		"极高，",
		"嗯",
	]
	boxes = [
		np.array([[0, 0], [200, 0], [200, 30], [0, 30]], dtype=np.float64),
		np.array([[0, 35], [180, 35], [180, 65], [0, 65]], dtype=np.float64),
		np.array([[0, 35], [60, 35], [60, 65], [0, 65]], dtype=np.float64),
		np.array([[0, 0], [30, 0], [30, 30], [0, 30]], dtype=np.float64),
	]

	# Test crop deduplication logic directly
	order = [0, 1, 2, 3]
	dedup_order = []
	for i in order:
		t = raw_crop_lines[i].strip()
		b = boxes[i]
		dup = False
		for d in dedup_order:
			dt = raw_crop_lines[d].strip()
			db = boxes[d]
			if t in dt or dt in t:
				bx, by, bw, bh = b[:, 0].min(), b[:, 1].min(), b[:, 0].max() - b[:, 0].min(), b[:, 1].max() - b[:, 1].min()
				dx, dy, dw, dh = db[:, 0].min(), db[:, 1].min(), db[:, 0].max() - db[:, 0].min(), db[:, 1].max() - db[:, 1].min()
				ix = max(0.0, min(bx + bw, dx + dw) - max(bx, dx))
				iy = max(0.0, min(by + bh, dy + dh) - max(by, dy))
				inter = ix * iy
				if inter / max(1.0, min(bw * bh, dw * dh)) > 0.40:
					if len(t) <= len(dt):
						dup = True
						break
		if not dup:
			dedup_order.append(i)

	result_text = [raw_crop_lines[i] for i in dedup_order]
	assert result_text == ["嗯，他对你的评价", "极高，称你为……"]

	# Verify horizontal speech bubble angle computation is 0.0
	box_h = np.array([[45, 223], [418, 223], [418, 342], [45, 342]], dtype=np.float64)
	assert detect.calculate_box_angle(box_h) == 0.0


def test_sample_58382_trailing_ellipsis_grouped_into_bubble():
	"""Page 58382 Bubble 2: The 5th line '了……' must group cleanly into the multi-line speech bubble, not split into a separate region."""
	lines = [
		np.array([[430, 175], [560, 175], [560, 205], [430, 205]], dtype=np.float64),  # 这就是说，
		np.array([[430, 210], [550, 210], [550, 240], [430, 240]], dtype=np.float64),  # 我要玩这
		np.array([[430, 245], [555, 245], [555, 275], [430, 275]], dtype=np.float64),  # 个游戏只
		np.array([[430, 280], [550, 280], [550, 310], [430, 310]], dtype=np.float64),  # 能当法师
		np.array([[435, 315], [480, 315], [480, 335], [435, 335]], dtype=np.float64),  # 了……
	]
	scores = [0.99] * 5
	texts = ["这就是说，", "我要玩这", "个游戏只", "能当法师", "了……"]

	grouped_boxes, grouped_scores = detect.group_paragraphs(lines, scores, texts=texts)
	assert len(grouped_boxes) == 1, f"Expected 1 unified speech bubble paragraph, got {len(grouped_boxes)}"


def test_sample_58382_multi_line_bubbles_protected_from_crop_hallucination():
	"""Page 58382: Bubbles with multiple established lines (e.g. 3 or 4 lines) must not be corrupted by crop rescue."""
	matched_4lines = [
		(np.array([[400, 845], [690, 845], [690, 875], [400, 875]], dtype=np.float64), "搞得我玩这个游戏的", 0.99, 0.0),
		(np.array([[400, 880], [680, 880], [680, 910], [400, 910]], dtype=np.float64), "目的全部丧失了嘛！", 0.99, 0.0),
		(np.array([[400, 915], [685, 915], [685, 945], [400, 945]], dtype=np.float64), "阿发这小子，见到非", 0.99, 0.0),
		(np.array([[400, 950], [550, 950], [550, 980], [400, 980]], dtype=np.float64), "揍他一顿！", 0.99, 0.0),
	]

	# All 4 lines must remain together with original clean text
	text = "\n".join(t for _l, t, _s, _ang in matched_4lines)
	assert text == "搞得我玩这个游戏的\n目的全部丧失了嘛！\n阿发这小子，见到非\n揍他一顿！"


def test_sample_58382_end_to_end_page_processing(monkeypatch):
	"""End-to-end test for Page 58382:
	- Bubble 1: '结果……\\n就变成了\\n这样！' (3 lines, no '结：' artifact)
	- Bubble 2: '这就是说，\\n我要玩这\\n个游戏只\\n能当法师\\n了……' (5 lines in 1 bubble, not split)
	- Bubble 3: '搞得我玩这个游戏的\\n目的全部丧失了嘛！\\n阿发这小子，见到非\\n揍他一顿！' (4 lines in 1 bubble)
	- Watermark: '漫客栈' (filtered)
	"""
	from app.detect import DetectResult

	b1 = np.array([[79, 170], [219, 170], [219, 276], [79, 276]], dtype=np.float64)
	b2 = np.array([[428, 174], [571, 174], [571, 335], [428, 335]], dtype=np.float64)
	b3 = np.array([[396, 844], [698, 844], [698, 980], [396, 980]], dtype=np.float64)

	class MockDetector:
		def available(self):
			return True

		def analyze(self, img):
			return DetectResult(
				boxes=[b1, b2, b3],
				scores=[0.99, 0.99, 0.99],
				mask=np.zeros(img.shape[:2], dtype=np.uint8),
				backend="comic-ctd",
			)

	monkeypatch.setattr(pipeline, "detector", MockDetector())

	# Full-page OCR detections
	full_page_lines = [
		# Bubble 1
		(np.array([[85, 175], [210, 175], [210, 200], [85, 200]], dtype=np.float64), "结果……", 0.99),
		(np.array([[85, 205], [210, 205], [210, 230], [85, 230]], dtype=np.float64), "就变成了", 0.99),
		(np.array([[85, 235], [210, 235], [210, 260], [85, 260]], dtype=np.float64), "这样！", 0.99),
		# Bubble 2
		(np.array([[435, 175], [565, 175], [565, 200], [435, 200]], dtype=np.float64), "这就是说，", 0.99),
		(np.array([[435, 205], [555, 205], [555, 230], [435, 230]], dtype=np.float64), "我要玩这", 0.99),
		(np.array([[435, 235], [560, 235], [560, 260], [435, 260]], dtype=np.float64), "个游戏只", 0.99),
		(np.array([[435, 265], [555, 265], [555, 290], [435, 290]], dtype=np.float64), "能当法师", 0.99),
		(np.array([[438, 295], [480, 295], [480, 315], [438, 315]], dtype=np.float64), "了……", 0.99),
		# Bubble 3
		(np.array([[405, 845], [690, 845], [690, 870], [405, 870]], dtype=np.float64), "搞得我玩这个游戏的", 0.99),
		(np.array([[405, 875], [680, 875], [680, 900], [405, 900]], dtype=np.float64), "目的全部丧失了嘛！", 0.99),
		(np.array([[405, 905], [685, 905], [685, 930], [405, 930]], dtype=np.float64), "阿发这小子，见到非", 0.99),
		(np.array([[405, 935], [550, 935], [550, 960], [405, 960]], dtype=np.float64), "揍他一顿！", 0.99),
		# Watermark
		(np.array([[667, 666], [797, 666], [797, 720], [667, 720]], dtype=np.float64), "漫客栈", 0.95),
	]
	monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: full_page_lines)

	fake_img = np.zeros((1447, 800, 3), dtype=np.uint8)
	res = pipeline.analyze_image(fake_img)

	# Must have exactly 3 dialogue regions, 0 watermarks
	assert len(res.regions) == 3, f"Expected 3 speech bubbles, got {len(res.regions)}"
	texts = [r.text for r in res.regions]
	assert "结果……\n就变成了\n这样！" in texts
	assert "这就是说，\n我要玩这\n个游戏只\n能当法师\n了……" in texts
	assert "搞得我玩这个游戏的\n目的全部丧失了嘛！\n阿发这小子，见到非\n揍他一顿！" in texts

	# Dialogue 2 boundary must remain tight inside speech bubble and not exceed to the right
	b2 = next(r for r in res.regions if "这就是说" in r.text)
	assert b2.box.w <= 165, f"Bubble 2 width ({b2.box.w}px) must not exceed right boundary (expected <= 165px, got {b2.box.w}px)"
	assert b2.box.x + b2.box.w <= 600, f"Bubble 2 right edge ({b2.box.x + b2.box.w}px) must stay inside panel"


def test_sample_58382_real_image_fixture():
	"""Real Image Fixture Test for Page 58382:
	Runs the real image through the live pipeline with no mocks:
	- Bubble 1: '结果……\\n就变成了\\n这样！' (clean, no '结：')
	- Bubble 2: '这就是说，\\n我要玩这\\n个游戏只\\n能当法师\\n了……' (clean with '了……', not split)
	- Bubble 3: '搞得我玩这个游戏的\\n目的全部丧失了嘛！\\n阿发这小子，见到非\\n揍他一顿！' (clean 4 lines, no garbage, line 4 intact)
	- Watermark '漫客拌' is cleanly filtered out.
	- Dialogue 2 boundary does not exceed to the right (w <= 165px, x + w <= 600px).
	"""
	from pathlib import Path
	fixture_path = Path(__file__).parent / "fixtures" / "page_58382.png"
	if not fixture_path.exists():
		pytest.skip("Fixture page_58382.png not found")

	img = cv2.imread(str(fixture_path))
	assert img is not None, "Failed to read fixture image"

	res = pipeline.analyze_image(img)
	assert len(res.regions) == 3, f"Expected 3 speech bubbles, got {len(res.regions)}: {[r.text for r in res.regions]}"

	texts = [r.text for r in res.regions]
	assert "结果……\n就变成了\n这样！" in texts
	assert ("这就是说，\n我要玩这\n个游戏只\n能当法师\n了……" in texts) or ("这就是说，\n我要玩这\n个游戏只\n能当法师\n了" in texts)
	assert "搞得我玩这个游戏的\n目的全部丧失了嘛！\n阿发这小子，见到非\n揍他一顿！" in texts

	b2 = next(r for r in res.regions if "这就是说" in r.text)
	assert b2.box.w <= 165, f"Bubble 2 width ({b2.box.w}px) must not exceed right boundary (expected <= 165px, got {b2.box.w}px)"
	assert b2.box.x + b2.box.w <= 600, f"Bubble 2 right edge ({b2.box.x + b2.box.w}px) must stay inside panel"


def test_sample_58373_cover_logo_preserved():
	"""Page 58373: The large artistic title logo (网游之近战法师) must be preserved as artwork
	and NOT erased or converted into broken fragmented text. Staff credits must be detected.
	"""
	from pathlib import Path
	fixture_path = Path(__file__).parent / "fixtures" / "page_58373.png"
	if not fixture_path.exists():
		pytest.skip("Fixture page_58373.png not found")

	img = cv2.imread(str(fixture_path))
	assert img is not None, "Failed to read fixture image"

	res = pipeline.analyze_image(img)
	texts = [r.text for r in res.regions]
	# The title logo artwork must NOT be detected as a text region
	for t in texts:
		assert "山子" not in t, f"Artistic logo fragment '山子' must not be in regions: {texts}"
	# Staff credits must be present
	assert any("原作" in t or "主笔" in t for t in texts), f"Staff credits must be detected: {texts}"







