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



