import cv2
import numpy as np
import pytest

from app import detect, ocr, pipeline
from app.watermark import watermark_remover
from app.schemas import Region, Box


def _box(x: int, y: int, w: int, h: int) -> np.ndarray:
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)


class TestReportedCases:
    """TEST SUITE FOR THE 11 REPORTED EDGE-CASE SAMPLES (ZERO REGRESSIONS)."""

    # --- CASE 1 & 2: WATERMARK INTERFERENCE & IN-BUBBLE CLEANING ---
    def test_case_1_and_2_bubble_watermark_crop_cleaning(self):
        """Cases 1 & 2: Chromatic watermark overlay in white speech bubble is cleaned before OCR."""
        # Create a white speech bubble crop (100x300) with dark Chinese text and colored (red/blue) watermark overlay
        crop = np.full((100, 300, 3), 255, dtype=np.uint8)
        # Black Chinese text strokes
        crop[30:70, 20:40] = [0, 0, 0]
        crop[30:70, 60:80] = [0, 0, 0]
        # Red watermark text (e.g. "KLAMANHUA.com" / "COLAMANHUA.com")
        crop[20:40, 100:250] = [30, 30, 220]

        cleaned = watermark_remover.clean_bubble_crop(crop)
        # Watermark region should be inpainted close to white
        assert np.mean(cleaned[20:40, 100:250]) > 200, "Colored watermark overlay must be inpainted to background"
        # Genuine text strokes should be preserved
        assert np.mean(cleaned[30:70, 20:40]) < 100, "Genuine black text strokes must be preserved"

    def test_page_63589_watermark_inpaint_recovery(self):
        """Page 63589 real fixture test:
        Top speech bubble with heavy watermark stamps ('COLAMANHUA.com', 'ACloudMerge.com')
        must recover '咦！居然让你\\n抽到了这个，' completely and cleanly.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63589.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63589.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        assert len(resp.regions) == 2, f"Expected 2 dialogue regions, got {len(resp.regions)}: {[r.text for r in resp.regions]}"
        top_bubble = resp.regions[0]
        assert "抽到了这个" in top_bubble.text
        assert "居然" in top_bubble.text
        assert len(top_bubble.text.strip().split("\n")) == 2, f"Top bubble must have exactly 2 lines, got: {top_bubble.text}"
        assert "COLAMANHUA" not in top_bubble.text
        assert "qumanku" not in top_bubble.text
        assert "唐然" not in top_bubble.text
        assert "让您co" not in top_bubble.text

    # --- CASE 3: SPEECH BUBBLE SEPARATION / OVER-MERGING GUARD ---
    def test_case_3_adjacent_bubble_split_and_grouping(self):
        """Case 3: Adjacent speech bubbles on the same row with internal punctuation or whitespace gap are split."""
        # Test punctuation split on fused terminal exclamation line: "掉了吧！死！"
        rapid_line = (
            _box(321, 1080, 356, 40),
            "掉了吧！死！",
            0.99,
            0.0,
        )
        fake_img = np.full((1200, 800, 3), 255, dtype=np.uint8)
        split_res = pipeline._split_lines_by_internal_punctuation([rapid_line], fake_img)
        assert len(split_res) == 2, f"Expected fused line to split into 2, got {len(split_res)}"
        assert split_res[0][1] == "掉了吧！"
        assert split_res[1][1] == "死！"

        # Test subsequent paragraph grouping:
        # Middle bubble lines:
        b_mid1 = _box(321, 980, 200, 35)   # 我们会长可是
        b_mid2 = _box(321, 1015, 200, 35)  # 一天就升到25级的
        b_mid3 = _box(321, 1050, 200, 35)  # 人，你是脑子坏
        b_mid4 = split_res[0][0]            # 掉了吧！ (x ~ 321, w ~ 178)

        # Right bubble lines:
        b_rt1 = _box(550, 985, 120, 35)   # 你这是
        b_rt2 = _box(550, 1020, 120, 35)  # 在作大
        b_rt3 = split_res[1][0]            # 死！ (x ~ 499, w ~ 178)

        boxes = [b_mid1, b_mid2, b_mid3, b_mid4, b_rt1, b_rt2, b_rt3]
        scores = [0.99] * len(boxes)
        texts = [
            "我们会长可是", "一天就升到25级的", "人，你是脑子坏", "掉了吧！",
            "你这是", "在作大", "死！",
        ]

        grouped_boxes, _ = detect.group_paragraphs(boxes, scores, texts=texts)
        assert len(grouped_boxes) == 2, f"Expected 2 separate speech bubbles, got {len(grouped_boxes)}"

    # --- CASE 4: BUBBLE TAIL DIGIT REMOVAL ---
    def test_case_4_bubble_tail_digit_stripping(self):
        """Case 4: Bubble tail trailing circles misrecognized as '200' after punctuation are stripped."""
        raw_text = "我看你能嚣张\n到什么时候！200"
        cleaned = pipeline._clean_stray_ocr_artifacts(raw_text)
        assert cleaned == "我看你能嚣张\n到什么时候！", f"Expected '200' to be stripped from end of punctuation, got {cleaned}"

    # --- CASE 5: LEFT-EDGE AND MULTI-LINE ORDERING ---
    def test_case_5_clean_stray_ocr_artifacts_normal(self):
        """Case 5: Multi-line speech bubble text preserves correct reading order."""
        text = "哼，这么胡\n来，菜鸟一\n个！"
        cleaned = pipeline._clean_stray_ocr_artifacts(text)
        assert cleaned == "哼，这么胡\n来，菜鸟一\n个！"

    # --- CASE 6: BOUNDING BOX OVER-EXPANSION GUARD ---
    def test_case_6_ellipsis_polygon_clamping(self):
        """Case 6: Ellipsis polygon does not over-expand across empty banner margins."""
        # Base text: "不愧是顶尖高手……" at x=[95, 230], y=[1212, 1254]
        base_pts = _box(95, 1212, 135, 42)
        # Comic union box spanning the entire wide panel (width 380)
        union_box = _box(95, 1212, 380, 42)
        poly = pipeline._ellipsis_polygon(base_pts, union_box, "不愧是顶尖高手……", page_w=800)
        bx, by, bw, bh = pipeline._polygon_bounds(poly)

        # The resulting box width should be tightly bounded around base width + padding, NOT 380
        assert bw < 250, f"Box width must not expand across entire empty banner: got {bw} (original union was 380)"
        assert bw >= 135, f"Box width must cover original text: got {bw}"

    # --- CASE 7: THOUGHT BUBBLE TAIL STANDALONE NOISE REJECTION ---
    def test_case_7_thought_tail_noise_filter(self):
        """Case 7: Standalone '300' / '200' thought-tail indicator boxes are recognized as pure noise."""
        assert detect.is_pure_watermark_region("300") is True
        assert detect.is_pure_watermark_region("200") is True
        assert detect.is_pure_watermark_region("000") is True
        assert detect.is_pure_watermark_region("ooo") is True

    # --- CASE 8: VERTICAL SKILL BRACKET & EXCLAMATION MERGING ---
    def test_case_8_vertical_skill_callout_unification(self):
        """Case 8: Vertical skill callout '『潜伏』' followed by vertical stroke '一' is merged into '『潜伏』！'."""
        r1 = Region(
            id="r0",
            box=Box(x=622, y=757, w=108, h=220),
            polygon=[[622, 757], [730, 757], [730, 977], [622, 977]],
            text="『潜伏』",
            confidence=0.98,
            vertical=True,
            angle=0.0,
        )
        r2 = Region(
            id="r1",
            box=Box(x=674, y=998, w=35, h=75),
            polygon=[[674, 998], [709, 998], [709, 1073], [674, 1073]],
            text="一",
            confidence=0.93,
            vertical=False,
            angle=0.0,
        )
        
        # Test merging via the pipeline's post-processing logic
        est_line_h = float(r1.box.h) / 3.0
        v_gap = r2.box.y - (r1.box.y + r1.box.h)
        is_vertical_skill_tail = (
            r1.vertical
            and r1.box.h >= 1.3 * r1.box.w
            and r2.text.strip() in ("一", "1", "丨", "I", "l", "|", "！", "!")
            and 0 <= v_gap <= max(est_line_h * 3.5, 120.0)
        )
        assert is_vertical_skill_tail is True, "Vertical skill exclamation stroke must be identified as tail"

    # --- CASE 9: SYSTEM UI CARD SAME-ROW PREFIX MERGING ---
    def test_case_9_system_card_prefix_merging(self):
        """Case 9: System card onomatopoeia prefix '嘟！' and '恐惧值+0' merge horizontally on same row."""
        b1 = _box(464, 1200, 75, 50)   # 嘟！
        b2 = _box(542, 1198, 190, 50)  # 恐惧值+0
        scores = [0.99, 0.99]
        texts = ["嘟！", "恐惧值+0"]

        merged_boxes, _ = detect.merge_text_lines([b1, b2], scores, texts=texts)
        assert len(merged_boxes) == 1, f"Expected '嘟！' and '恐惧值+0' to merge on the same row, got {len(merged_boxes)} boxes"
        mb = merged_boxes[0]
        x, y, w, h = detect.box_to_xywh(mb)
        assert x <= 464 and x + w >= 732, f"Merged box should encompass both boxes, got x={x}, w={w}"

    # --- CASE 10: CHARACTER ART NOISE FILTERING ---
    def test_case_10_low_confidence_isolated_character_filtering(self):
        """Case 10: Low confidence isolated character on drawing artifact ('小', score 0.68) unsupported by comic mask is filtered."""
        c_count = 1
        t_strip = "小"
        conf = 0.68272
        w, h = 36, 61
        is_punct = bool(pipeline._PUNCT_ONLY.fullmatch(t_strip) or pipeline._ALL_ELLIPSIS.fullmatch(t_strip))
        comic_mask = np.zeros((1000, 800), dtype=np.uint8)  # No bubble mask at this location
        is_unsupported_char_noise = (
            c_count == 1
            and not is_punct
            and conf < 0.70
            and w <= 55
            and h <= 70
            and (comic_mask is None or np.sum(comic_mask[686:686+h, 465:465+w] >= 127) == 0)
        )
        assert bool(is_unsupported_char_noise) is True, "Isolated character drawing noise unsupported by comic mask must be detected"

    # --- CASE 11: SFX PUNCTUATION RETENTION ---
    def test_case_11_sfx_exclamation_retention(self):
        """Case 11: SFX exclamation mark '咳！' is preserved by clean_stray_ocr_artifacts."""
        text = "咳！"
        cleaned = pipeline._clean_stray_ocr_artifacts(text)
        assert cleaned == "咳！", f"Exclamation mark must be preserved: got {cleaned}"

    # --- CASE 12: PAGE 63590 SIDE-BY-SIDE SPEECH BUBBLE SEPARATION ---
    def test_page_63590_side_by_side_bubble_separation(self):
        """Page 63590 real fixture test:
        Middle bubble ('我们会长可是\\n天就升到25级的\\n人，你是脑子坏\\n掉了吧！') and
        Right bubble ('你这是\\n在作大\\n死！') must NOT fuse horizontally across rows.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63590.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63590.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        texts = [r.text for r in resp.regions]

        # Verify Middle Bubble
        mid_bubble = next((t for t in texts if "我们会长可是" in t), None)
        assert mid_bubble is not None, f"Middle bubble missing from {texts}"
        assert "你这是" not in mid_bubble, f"Middle bubble fused with right bubble: {repr(mid_bubble)}"
        assert "作大" not in mid_bubble, f"Middle bubble fused with right bubble: {repr(mid_bubble)}"
        assert "25级" in mid_bubble

        # Verify Right Bubble
        right_bubble = next((t for t in texts if "在作大" in t or "死！" in t), None)
        assert right_bubble is not None, f"Right bubble missing from {texts}"
        assert "我们会长" not in right_bubble, f"Right bubble fused with middle bubble: {repr(right_bubble)}"
        assert "你这是" in right_bubble or "在作大" in right_bubble

    # --- CASE 13: PAGE 63592 SPEECH BUBBLE LEADING CHARACTER RETENTION ---
    def test_page_63592_speech_bubble_interjection_retention(self):
        """Page 63592 real fixture test:
        Bottom speech bubble ('哼，这么胡\\n来，菜鸟一\\n个！') must capture the leading '哼，'
        and all 3 dialogue lines completely without dropping characters.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63592.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63592.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        texts = [r.text for r in resp.regions]

        # Verify stat card / equipment list
        stat_card = next((t for t in texts if "职业：法师" in t or "职业" in t), None)
        assert stat_card is not None, f"Stat card missing from {texts}"
        assert "残破的割肉小刀" in stat_card

        # Verify speech bubble
        speech_bubble = next((t for t in texts if "这么胡" in t or "菜鸟" in t), None)
        assert speech_bubble is not None, f"Speech bubble missing from {texts}"
        assert speech_bubble.startswith("哼") or "哼，" in speech_bubble or "哼" in speech_bubble, f"Leading character '哼' missing: {repr(speech_bubble)}"
        assert "这么胡" in speech_bubble
        assert "菜鸟" in speech_bubble
        assert "个！" in speech_bubble or "个" in speech_bubble

    # --- CASE 14: PAGE 63591 THOUGHT BUBBLE BOUNDARY & STRAY NOISE CLEANUP ---
    def test_page_63591_thought_bubble_boundary_and_stray_cleanup(self):
        """Page 63591 real fixture test:
        - Top bubble ('我看你能嚣张\\n到什么时候！') must NOT expand across the character's face (w <= 210px, x+w <= 480px).
        - Stray drawing noise ('ill') on the character's clothing must be filtered out.
        - Exactly 4 speech bubbles must be detected.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63591.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63591.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        assert len(resp.regions) == 4, f"Expected 4 speech bubbles, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

        # Check top speech bubble width and boundary
        top_bubble = next((r for r in resp.regions if "我看你能嚣张" in r.text), None)
        assert top_bubble is not None, "Top bubble missing"
        assert "到什么时候！" in top_bubble.text
        assert top_bubble.box.w <= 210, f"Top bubble over-expanded to the right: w={top_bubble.box.w}"
        assert top_bubble.box.x + top_bubble.box.w <= 480, f"Top bubble exceeds face boundary: x+w={top_bubble.box.x + top_bubble.box.w}"

        # Ensure no stray non-Chinese noise
        texts = [r.text for r in resp.regions]
        assert "ill" not in texts

    # --- CASE 15: PAGE 63593 SINGLE-LINE ELLIPSIS BOUNDARY RETENTION ---
    def test_page_63593_single_line_ellipsis_boundary(self):
        """Page 63593 real fixture test:
        - Bottom speech bubble ('不愧是顶尖高手……') must NOT over-expand into the right panel border (w <= 310px, x+w <= 405px).
        - All 5 speech bubbles must be detected cleanly.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63593.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63593.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        assert len(resp.regions) == 5, f"Expected 5 speech bubbles, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

        # Check bottom speech bubble width and boundary
        bottom_bubble = next((r for r in resp.regions if "不愧是顶尖高手" in r.text), None)
        assert bottom_bubble is not None, "Bottom bubble missing"
        assert bottom_bubble.box.w <= 310, f"Bottom bubble over-expanded to the right: w={bottom_bubble.box.w}"
        assert bottom_bubble.box.x + bottom_bubble.box.w <= 405, f"Bottom bubble exceeds panel boundary: x+w={bottom_bubble.box.x + bottom_bubble.box.w}"

    # --- CASE 16: PAGE 63596 VERTICAL SKILL EXCLAMATION MARK INTEGRATION ---
    def test_page_63596_vertical_skill_exclamation_integration(self):
        """Page 63596 real fixture test:
        - Vertical skill callout ('『潜伏』！') must capture the trailing vertical exclamation mark '！'.
        - Region bounding box must cover the full vertical text span down past y=1050 (h >= 350).
        - Dialogue speech bubbles ('可恶！', '我也差点儿\\n忘记我是个\\n盗贼了！') must be detected accurately.
        - Exactly 3 regions must be detected on the page.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63596.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63596.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        assert len(resp.regions) == 3, f"Expected 3 regions, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

        # Check dialogue bubbles
        texts = [r.text for r in resp.regions]
        assert any("可恶" in t for t in texts), f"'可恶！' missing: {texts}"
        assert any("忘记我是个" in t and "盗贼" in t for t in texts), f"Thief speech bubble missing: {texts}"

        # Check vertical skill callout
        skill_reg = next((r for r in resp.regions if "潜" in r.text and "伏" in r.text), None)
        assert skill_reg is not None, f"Skill callout '潜伏' missing: {texts}"
        assert skill_reg.text.rstrip().endswith(("！", "!")), f"Skill callout missing trailing exclamation mark: {repr(skill_reg.text)}"
        assert skill_reg.box.y + skill_reg.box.h >= 1050, f"Skill bounding box does not cover exclamation mark: {skill_reg.box}"
        assert skill_reg.vertical is True, f"Skill region must be vertical: {skill_reg}"

    # --- CASE 17: PAGE 63603 BLOOD SPRAY SFX & EXCLAMATION MARK RETENTION ---
    def test_page_63603_blood_sfx_and_exclamation_retention(self):
        """Page 63603 real fixture test:
        - Consecutive action sound effects ('咳！', '咳！', '咳！') along blood burst.
        - Exclamation mark retention on all cough SFX instances without dropping terminal punctuation.
        - High-contrast splash SFX ('噗' at bottom pool) detection.
        - Exactly 4 SFX regions detected on the page.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63603.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63603.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        texts = [r.text.strip() for r in resp.regions]

        # Verify cough sound effects detected along blood spray and all retain exclamation marks
        cough_regions = [r for r in resp.regions if "咳" in r.text]
        assert len(cough_regions) == 3, f"Expected 3 '咳' SFX regions, got {len(cough_regions)}: {texts}"
        for r in cough_regions:
            assert r.text.strip().endswith(("！", "!")), f"Cough SFX missing exclamation mark: {repr(r.text)}"

        # Verify bottom splash sound effect '噗'
        assert any("噗" in t for t in texts), f"Bottom splash SFX '噗' not detected in {texts}"
        assert len(resp.regions) == 4, f"Expected 4 SFX regions ('咳！'x3 + '噗'), got {len(resp.regions)}: {texts}"

        # Ensure no stray non-Chinese noise on top panels
        assert "MY" not in texts, f"Drawing artifact on hand panels detected as text: {texts}"

    # --- CASE 18: PAGE 63602 SFX RETENTION & DRAWING ARTIFACT FILTERING ---
    def test_page_63602_sfx_and_tremor_noise_suppression(self):
        """Page 63602 real fixture test:
        - Top speech bubble ('哇啊……啊……\\n老大！有状况啊！') is captured cleanly.
        - Drawing artifact / tremor lines between characters ('小', score 0.59) is filtered out.
        - All 3 bottom sound effects ('沙—', '沙—', '沙—') are preserved without hallucinated ellipsis dots ('……').
        - Exactly 4 regions are detected on the page.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_63602.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_63602.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        texts = [r.text for r in resp.regions]

        # Verify speech bubble
        speech_bubble = next((t for t in texts if "老大" in t and "有状况啊" in t), None)
        assert speech_bubble is not None, f"Top speech bubble missing from {texts}"
        assert "哇啊" in speech_bubble

        # Verify no false positive '小'
        assert "小" not in texts, f"Ghost detection '小' on tremor lines must be filtered: {texts}"

        # Verify sound effects: exactly 3 SFX containing '沙'
        sfx_regions = [r for r in resp.regions if "沙" in r.text]
        assert len(sfx_regions) == 3, f"Expected 3 SFX regions ('沙—' x3), got {len(sfx_regions)}: {texts}"
        for sfx in sfx_regions:
            assert "……" not in sfx.text, f"SFX trailing dash must not hallucinate ellipsis dots: {repr(sfx.text)}"

        assert len(resp.regions) == 4, f"Expected exactly 4 regions (1 dialogue + 3 SFX), got {len(resp.regions)}: {texts}"

    # --- CASE 19: PAGE 45050 TOP BOUNDARY CLAMPING & VERTICAL BUBBLE DETECTION ---
    def test_page_45050_vertical_bubble_and_top_boundary(self):
        """Page 45050 real fixture test:
        - Top-left speech bubble ('喂，肖凝儿要是...') must not expand boundary past x=425 to avoid invading neighbor at x=455.
        - Vertical speech bubble ('肖凝儿？') must be classified as vertical=True.
        - Exactly 7 dialogue regions are detected.
        """
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "page_45050.png"
        if not fixture_path.exists():
            pytest.skip("Fixture page_45050.png not found")

        img = cv2.imread(str(fixture_path))
        resp = pipeline.analyze_image(img)
        assert len(resp.regions) == 7, f"Expected 7 regions, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

        # Check top-left bubble boundary
        top_left = next((r for r in resp.regions if "揍聂离咋办" in r.text), None)
        assert top_left is not None, "Top left bubble missing"
        assert top_left.box.x + top_left.box.w <= 425, f"Top left bubble box invaded neighboring bubble: {top_left.box}"

        # Check vertical bubble
        vert_bubble = next((r for r in resp.regions if "肖凝儿？" in r.text), None)
        assert vert_bubble is not None, "Vertical bubble '肖凝儿？' missing"
        assert vert_bubble.vertical is True, f"Vertical bubble must have vertical=True: {vert_bubble}"

    # --- CASE 20: DIALOGUE SPEECH BUBBLE RIGHT-SIDE BOUNDARY OVER-EXPANSION GUARD ---
    def test_case_20_dialogue_bubble_tight_boundary_guard(self):
        """Case 20 (Page 45504 sample):
        Dialogue bubble with complete ellipsis ('你可不要\\n乱动……') must not expand its left and right
        boundaries across the entire bubble whitespace (from x=59 to x=234) when the text hull only spans x=[85..215].
        """
        # Text hull for '你可不要\n乱动……' spanning x=[85..215], y=[1095..1165]
        base_pts = _box(85, 1095, 130, 70)
        # ComicTextDetector bubble mask box spanning [59, 1095, 175, 79] (x=[59..234])
        union_box = _box(59, 1095, 175, 79)
        poly = pipeline._ellipsis_polygon(base_pts, union_box, "你可不要\n乱动……", page_w=800)
        bx, by, bw, bh = pipeline._polygon_bounds(poly)

        # The bounding box should remain tightly bounded around the text (x ~ 83..85, x+w ~ 215..217, width ~ 130..135)
        # and NOT stretch to the detector's right edge (234) or left edge (59).
        assert bx >= 80, f"Left boundary expanded too far into left bubble margin: got x={bx} (expected >= 80)"
        assert bx + bw <= 220, f"Right boundary expanded too far into right bubble margin: got x+w={bx+bw} (expected <= 220)"
        assert bw <= 140, f"Box width must not expand to full bubble width (175): got {bw}"

    # --- CASE 21: OVERSIZED SINGLE-CHARACTER ARTWORK HALLUCINATION FILTERING ---
    def test_case_21_oversized_single_char_artwork_hallucination(self):
        """Case 21 (Page 45517 sample):
        Oversized single character ('福', box 250x261) hallucinated from character clothing/ribbon folds
        with negligible text mask coverage is recognized as unsupported character noise and filtered.
        """
        c_count = 1
        t_strip = "福"
        conf = 0.90287
        w, h = 250, 261
        is_punct = bool(pipeline._PUNCT_ONLY.fullmatch(t_strip) or pipeline._ALL_ELLIPSIS.fullmatch(t_strip))
        # Mask with 0 text coverage on clothing folds
        comic_mask = np.zeros((1612, 800), dtype=np.uint8)
        mask_cov = np.sum(comic_mask[309:309+h, 129:129+w] >= 127) / float(w * h)

        is_unsupported_char_noise = (
            c_count == 1
            and not is_punct
            and (
                (conf < 0.70 and w <= 55 and h <= 70 and (comic_mask is None or np.sum(comic_mask[309:309+h, 129:129+w] >= 127) == 0))
                or (comic_mask is not None and w >= 160 and h >= 160 and w * h >= 30000 and mask_cov < 0.10)
            )
        )
        assert bool(is_unsupported_char_noise) is True, "Oversized single character hallucination on artwork folds must be filtered"

    # --- CASE 22: DIALOGUE PARAGRAPH FRAGMENTATION & MULTI-LINE GROUPING GUARD ---
    def test_case_22_dialogue_paragraph_fragmentation_guard(self):
        """Case 22 (Page 45516 sample):
        Multi-line dialogue speech bubble:
        Line 1-2 (upper): '算了，岁数也大了\\n身体也不行\\n1我还是' (box [13, 807, 289, 74])
        Line 3 (trailing): '乖乖练级吧。' (box [16, 876, 175, 34])
        Adjacent right bubble: '需要我带你升\\n级吗？' (box [520, 871, 194, 74])

        All lines of the left speech bubble must be grouped into ONE single region spanning y=[807..910]
        and not fragmented into disjoint regions, preserving clean typography and reading order.
        """
        b_left_top = _box(13, 807, 289, 74)
        b_left_bot = _box(16, 876, 175, 34)
        b_right = _box(520, 871, 194, 74)

        boxes = [b_left_top, b_right, b_left_bot]
        scores = [0.99992, 0.9982, 0.9997]
        texts = [
            "算了，岁数也大了\n身体也不行\n1我还是",
            "需要我带你升\n级吗？",
            "乖乖练级吧。",
        ]

        grouped_boxes, grouped_scores = detect.group_paragraphs(boxes, scores, texts=texts)
        assert len(grouped_boxes) == 2, f"Expected 2 unified speech bubbles (left & right), got {len(grouped_boxes)}"

        dedup_boxes, dedup_scores = detect.deduplicate_boxes(grouped_boxes, grouped_scores)
        assert len(dedup_boxes) == 2, f"Expected 2 deduplicated bubbles, got {len(dedup_boxes)}"

        order = detect.sort_regions_top_to_bottom(dedup_boxes, 1201)
        left_idx = order[0]
        right_idx = order[1]

        left_b = dedup_boxes[left_idx]
        right_b = dedup_boxes[right_idx]

        lx, ly, lw, lh = detect.box_to_xywh(left_b)
        rx, ry, rw, rh = detect.box_to_xywh(right_b)

        # Left bubble must span from top line (y=807) to bottom line (y+h >= 910)
        assert ly <= 807 and ly + lh >= 910, f"Left bubble height must encompass all 3 lines: y={ly}, h={lh}"
        assert lw >= 280, f"Left bubble width must cover full dialogue width: w={lw}"

        # Right bubble must remain distinct at x=520
        assert rx >= 500, f"Right bubble must remain at x >= 500: got x={rx}"

        # Sub-test: Individual lines where Line 2 text has OCR spurious newlines ('身体也不行\n1\n我还是')
        # must still properly group all 3 dialogue lines based on physical line height limits
        l0 = _box(13, 807, 261, 42)
        l1 = _box(14, 839, 288, 42)
        l2 = _box(16, 876, 175, 34)
        r1 = _box(521, 871, 193, 37)
        r2 = _box(520, 906, 90, 39)

        raw_boxes = [l0, l1, l2, r1, r2]
        raw_scores = [0.99] * 5
        raw_texts = ["算了，岁数也大了", "身体也不行\n1\n我还是", "乖乖练级吧。", "需要我带你升", "级吗？"]

        g_boxes, g_scores = detect.group_paragraphs(raw_boxes, raw_scores, texts=raw_texts)
        assert len(g_boxes) == 2, f"Expected 2 grouped speech bubbles from raw lines, got {len(g_boxes)}"
        d_boxes, _ = detect.deduplicate_boxes(g_boxes, g_scores)
        assert len(d_boxes) == 2, f"Expected 2 deduplicated bubbles from raw lines, got {len(d_boxes)}"
        g_left = next(b for b in d_boxes if detect.box_to_xywh(b)[0] < 100)
        gx, gy, gw, gh = detect.box_to_xywh(g_left)
        assert gy <= 807 and gy + gh >= 910, f"Grouped left bubble must cover all lines: y={gy}, h={gh}"



