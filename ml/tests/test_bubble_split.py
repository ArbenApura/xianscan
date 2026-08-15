import os
import sys
from pathlib import Path
import numpy as np
import pytest
import cv2

from app import detect, ocr, pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_merge_text_lines_terminal_punct_guard():
    """Terminal punctuation guard prevents horizontal merge of distinct utterances."""
    b1 = np.array([[49, 105], [253, 105], [253, 152], [49, 152]], dtype=np.float64)
    b2 = np.array([[273, 105], [352, 105], [352, 152], [273, 152]], dtype=np.float64)
    merged, _ = detect.merge_text_lines([b1, b2], [0.99, 0.96], texts=["裤子上不可！", "哈哈！"])
    assert len(merged) == 2, f"Expected 2 separate boxes, got {len(merged)}"


def test_split_lines_by_internal_punctuation_synthetic(monkeypatch):
    """Internal sentence-terminal punctuation splits fused lines when OCR recognizes both pieces."""
    rapid_line = (
        np.array([[49, 105], [357, 105], [357, 152], [49, 152]], dtype=np.float64),
        "裤子上不可！哈哈！",
        0.99,
        0.0,
    )
    img = np.full((300, 400, 3), 255, dtype=np.uint8)
    
    # Mock recognize_line to return the sub-strings
    def mock_rec(crop):
        w = crop.shape[1]
        if w > 150:
            return ocr.OcrResult(text="裤子上不可！", score=0.99)
        return ocr.OcrResult(text="哈哈！", score=0.96)
    
    monkeypatch.setattr(ocr, "recognize_line", mock_rec)
    res = pipeline._split_lines_by_internal_punctuation([rapid_line], img)
    assert len(res) == 2
    assert res[0][1] == "裤子上不可！"
    assert res[1][1] == "哈哈！"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_683.jpg").exists(),
    reason="Page 683 sample fixture not found",
)
def test_page_683_full_pipeline_bubble_separation():
    """Page 683 regression test: Left bubble ('这傻子非得尿\\n裤子上不可！') and right bubble ('哈哈！')
    must be detected as two distinct dialogue regions, NOT merged into one.
    """
    img_path = FIXTURES_DIR / "page_683.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())
    
    resp = pipeline.analyze_image(img)
    
    left_bubble = next((r for r in resp.regions if "裤子上不可" in r.text or "这傻子非得尿" in r.text), None)
    right_bubble = next((r for r in resp.regions if "哈哈" in r.text), None)
    
    assert left_bubble is not None, "Left bubble region must be detected"
    assert right_bubble is not None, "Right bubble region must be detected"
    assert left_bubble.id != right_bubble.id, "Left and right speech bubbles must have separate region IDs"
    assert "哈哈" not in left_bubble.text, f"Left bubble text should not contain '哈哈': {left_bubble.text}"
    assert "裤子" not in right_bubble.text, f"Right bubble text should not contain '裤子': {right_bubble.text}"
    assert left_bubble.text == "这傻子非得尿\n裤子上不可！", f"Left bubble text must have clean single exclamation mark: {left_bubble.text}"
    
    # Verify no horizontal box intrusion
    left_right_x = left_bubble.box.x + left_bubble.box.w
    assert left_right_x <= right_bubble.box.x + 5, f"Left bubble box (ends at {left_right_x}) must not invade right bubble box (starts at {right_bubble.box.x})"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_679.jpg").exists(),
    reason="Page 679 sample fixture not found",
)
def test_page_679_full_pipeline_text_completeness():
    """Page 679 regression test: First bubble must contain full text '难道这么多年张予德都在成都和你们在一起？'
    and must not be fragmented or chopped.
    """
    img_path = FIXTURES_DIR / "page_679.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())
    
    resp = pipeline.analyze_image(img)
    
    first_bubble = next((r for r in resp.regions if "张予德" in r.text or "成都" in r.text or "难道" in r.text), None)
    assert first_bubble is not None, "First speech bubble must be detected"
    
    # Check that key beginning and end characters are present
    assert "难道" in first_bubble.text or "道这么" in first_bubble.text, f"Beginning of first bubble must be present: {first_bubble.text}"
    assert "难道这么多" in first_bubble.text, f"First line must be complete: {first_bubble.text}"
    assert "年张予德都" in first_bubble.text, f"Second line must be complete: {first_bubble.text}"
    assert "在成都和你" in first_bubble.text, f"Third line must be complete: {first_bubble.text}"
    assert "们在一起？" in first_bubble.text, f"Fourth line must be complete: {first_bubble.text}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_688.jpg").exists(),
    reason="Page 688 sample fixture not found",
)
def test_page_688_narration_panel_detected():
    """Page 688 regression test: Middle-right panel narration text
    '但是在光辉之城受到袭击的时候，神圣世家却背叛了光辉之城，弃城而逃。'
    must be detected as a region and not erased by watermark filters.
    """
    img_path = FIXTURES_DIR / "page_688.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    narration = next((r for r in resp.regions if ("光辉之城" in r.text or "神圣世家" in r.text) and "背叛" in r.text), None)
    assert narration is not None, "Middle-right narration panel must be detected"
    assert "受到袭击" in narration.text, f"Narration text must include '受到袭击': {narration.text}"
    assert "背叛了" in narration.text, f"Narration text must include '背叛了': {narration.text}"
    assert "弃城而逃" in narration.text, f"Narration text must include '弃城而逃': {narration.text}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_1057.png").exists(),
    reason="Page 1057 sample fixture not found",
)
def test_page_1057_bubble_not_merged_with_bottom_watermark():
    """Page 1057 regression test: Right speech bubble in panel 2 ('它们具有极强的领地意识...')
    must NOT merge with the distant watermark at the bottom of the page (y=2197).
    The bounding box height must stay tightly bounded to panel 2 (~188px, NOT 1059px).
    """
    img_path = FIXTURES_DIR / "page_1057.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    right_bubble = next((r for r in resp.regions if "领地意识" in r.text or "犹豫的发动" in r.text), None)
    assert right_bubble is not None, "Right speech bubble must be detected"

    # Box must be strictly bounded to panel 2
    assert right_bubble.box.y >= 1150 and right_bubble.box.y <= 1250, f"Y start should be in panel 2: {right_bubble.box}"
    assert right_bubble.box.h <= 260, f"Height should be ~188px (compact bubble), not multi-panel ({right_bubble.box.h}px)"
    assert (right_bubble.box.y + right_bubble.box.h) <= 1500, f"Bottom of bubble should not extend beyond panel 2: {right_bubble.box}"

    # Text must not have duplicate terminal exclamation marks
    assert right_bubble.text.endswith("击！"), f"Text should end with clean single punctuation: {repr(right_bubble.text)}"
    assert not right_bubble.text.endswith("击！!"), f"Text must not have duplicated exclamation mark: {repr(right_bubble.text)}"


def test_punctuation_merge_rejects_distant_noise_and_duplicate_terminal(monkeypatch):
    """Punctuation merge must not merge a distant punctuation/watermark across panels."""
    class FakeDetector:
        def available(self):
            return False

    monkeypatch.setattr(pipeline, "detector", FakeDetector())

    # Simulated 2 contiguous dialogue lines ending in '！'
    distant_wm = np.array([[663, 2197], [795, 2197], [795, 2258], [663, 2258]], dtype=np.float64)

    lines = [
        (np.array([[590, 1200], [720, 1200], [720, 1230], [590, 1230]], dtype=np.float64), "领地意识，", 0.99),
        (np.array([[590, 1232], [630, 1232], [630, 1262], [590, 1262]], dtype=np.float64), "击！", 0.99),
        (distant_wm, "!", 0.64),
    ]

    monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda _img: lines)
    monkeypatch.setattr(pipeline.ocr, "recognize_crop", lambda _img: None)

    resp = pipeline.analyze_image(np.zeros((2284, 800, 3), dtype=np.uint8))
    target = next((r for r in resp.regions if "领地意识" in r.text), None)
    assert target is not None
    assert target.box.h < 300, f"Box height must not encompass distant watermark: {target.box.h}"
    assert target.text.endswith("击！")
    assert not target.text.endswith("击！!")
    # Distant watermark '!' must not exist as a region either
    assert not any(r.text.strip() == "!" for r in resp.regions)


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_1062.png").exists(),
    reason="Page 1062 sample fixture not found",
)
def test_page_1062_vertical_multiline_bubble_rescue():
    """Page 1062 regression test: Bottom-right character panel's 3-line speech bubble
    '又干\\n掉一\\n只！' must be cleanly recognized as all 3 lines, NOT garbled into '对期'.
    """
    img_path = FIXTURES_DIR / "page_1062.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    bubble = next((r for r in resp.regions if 1900 <= r.box.y <= 2150 and 400 <= r.box.x <= 520), None)
    assert bubble is not None, "Bottom-right speech bubble must be detected"
    assert "对期" not in bubble.text, f"Text must not contain garbled OCR '对期': {repr(bubble.text)}"
    assert "又干" in bubble.text, f"Text must contain line 1 '又干': {repr(bubble.text)}"
    assert "掉一" in bubble.text, f"Text must contain line 2 '掉一': {repr(bubble.text)}"
    assert "只！" in bubble.text or "只!" in bubble.text, f"Text must contain line 3 '只！': {repr(bubble.text)}"
    assert bubble.confidence >= 0.70, f"Confidence should be high (>0.70): {bubble.confidence}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_1070.png").exists(),
    reason="Page 1070 sample fixture not found",
)
def test_page_1070_sfx_not_merged_into_monologue_bubble():
    """Page 1070 regression test: Panel 5 black monologue bubble ('居然这\\n样盯着\\n我看，\\n太无礼\\n了!')
    must NOT merge with Nie Li's larger SFX text ('打量') on the left.
    The monologue box must form 1 complete unified region containing all 5 lines,
    and '打量' must stay as its own separate region.
    """
    img_path = FIXTURES_DIR / "page_1070.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    monologue = next((r for r in resp.regions if "居然这" in r.text or "太无礼" in r.text), None)
    assert monologue is not None, "Monologue speech bubble must be detected"

    # Monologue must contain all 5 lines without being fragmented
    assert "居然这" in monologue.text, f"Line 1 '居然这' missing from: {repr(monologue.text)}"
    assert "样盯着" in monologue.text, f"Line 2 '样盯着' missing from: {repr(monologue.text)}"
    assert "我看" in monologue.text, f"Line 3 '我看' missing from: {repr(monologue.text)}"
    assert "太无礼" in monologue.text, f"Line 4 '太无礼' missing from: {repr(monologue.text)}"
    assert "打量" not in monologue.text, f"SFX '打量' must NOT be merged into monologue: {repr(monologue.text)}"

    # Box x must be centered around x=436 (not ballooned leftwards to x=345)
    assert monologue.box.x >= 420, f"Monologue box should start around x=436: {monologue.box}"
    assert monologue.box.w <= 90, f"Monologue box width should be compact (~71px), not ballooned ({monologue.box.w}px)"

    # '打量' should exist as its own separate region on the left
    sfx = next((r for r in resp.regions if "打量" in r.text), None)
    assert sfx is not None, "SFX '打量' should be detected as its own region"
    assert sfx.box.x < 400, f"SFX box should be on the left (x ~ 345): {sfx.box}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_1088.jpg").exists(),
    reason="Page 1088 sample fixture not found",
)
def test_page_1088_bubble_paragraphs_unified():
    """Page 1088 regression test:
    1. Left bubble ('虽然我天赋很差，\\n那又怎样？\\n迟早\\n有一天，') must NOT be split into two.
    2. Right bubble ('我会成为像叶墨大\\n人那样的传奇妖灵\\n师,而且我要娶光辉\\n之城最美的女人！')
       must NOT split '之城最美的女人！' into a separate region.
    """
    img_path = FIXTURES_DIR / "page_1088.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # 1. Left bubble test
    left_bubble = next((r for r in resp.regions if "天赋很差" in r.text or "那又怎样" in r.text), None)
    assert left_bubble is not None, "Left speech bubble must be detected"
    assert "天赋很差" in left_bubble.text, f"Line 1 '天赋很差' missing: {repr(left_bubble.text)}"
    assert "那又怎样" in left_bubble.text, f"Line 2 '那又怎样' missing: {repr(left_bubble.text)}"
    assert "有一天" in left_bubble.text, f"Line 3 '有一天' missing: {repr(left_bubble.text)}"
    assert left_bubble.box.h >= 80, f"Left bubble should span all 3 lines (h >= 80): {left_bubble.box}"

    # 2. Right bubble test
    right_bubble = next((r for r in resp.regions if "叶墨" in r.text or "最美的女人" in r.text), None)
    assert right_bubble is not None, "Right speech bubble must be detected"
    assert "我会成为像叶墨" in right_bubble.text or "叶墨" in right_bubble.text, f"Line 1 missing: {repr(right_bubble.text)}"
    assert "传奇妖灵" in right_bubble.text, f"Line 2 missing: {repr(right_bubble.text)}"
    assert "而且我要娶光辉" in right_bubble.text or "娶光辉" in right_bubble.text, f"Line 3 missing: {repr(right_bubble.text)}"
    assert "最美的女人" in right_bubble.text, f"Line 4 '最美的女人' missing: {repr(right_bubble.text)}"
    assert right_bubble.box.h >= 130, f"Right bubble should span all 4 lines (h >= 130): {right_bubble.box}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_825.jpg").exists(),
    reason="Page 825 sample fixture not found",
)
def test_page_825_vertical_bubbles_stay_upright():
    """Page 825 regression test:
    Vertical dialogue bubbles ('叽叽喳喳', '吵闹') must have angle=0.0 so their English
    translations ('CHATTER', 'NOISY') are typeset horizontally (upright), NOT rotated 90° sideways.
    """
    img_path = FIXTURES_DIR / "page_825.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    chatter = next((r for r in resp.regions if "叽叽喳喳" in r.text), None)
    assert chatter is not None, "Vertical bubble '叽叽喳喳' must be detected"
    assert chatter.angle == 0.0, f"Vertical bubble must have angle 0.0 (upright), got {chatter.angle}"

    noisy_vert = next((r for r in resp.regions if "吵" in r.text and r.box.y >= 900), None)
    assert noisy_vert is not None, "Vertical bubble '吵闹' in panel 3 must be detected"
    assert noisy_vert.angle == 0.0, f"Vertical bubble must have angle 0.0 (upright), got {noisy_vert.angle}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_828.jpg").exists(),
    reason="Page 828 sample fixture not found",
)
def test_page_828_stacked_bubble_lines_unified():
    """Page 828 regression test:
    The 4-line speech bubble ('往聂\\n离那\\n里去\\n了！') must be grouped into a single unified
    region spanning all 4 lines, not split into ('往聂离那') and ('里去了！').
    """
    img_path = FIXTURES_DIR / "page_828.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    bubble = next((r for r in resp.regions if "往聂" in r.text or "里去" in r.text), None)
    assert bubble is not None, "Speech bubble '往聂离那里去了！' must be detected"
    assert "往聂" in bubble.text, f"Line 1 '往聂' missing: {repr(bubble.text)}"
    assert "离那" in bubble.text, f"Line 2 '离那' missing: {repr(bubble.text)}"
    assert "里去" in bubble.text, f"Line 3 '里去' missing: {repr(bubble.text)}"
    assert "了" in bubble.text, f"Line 4 '了！' missing: {repr(bubble.text)}"
    assert bubble.box.h >= 100, f"Bubble box height should cover all 4 lines (h >= 100): {bubble.box}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58442.png").exists(),
    reason="Page 58442 sample fixture not found",
)
def test_page_58442_numeric_prefix_preserved():
    """Page 58442 regression test:
    Numeric prefixes before Chinese text (e.g. '1000000恐惧值' and '售价：')
    must be fully preserved and NOT stripped away by OCR / cleanup filters.
    """
    img_path = FIXTURES_DIR / "page_58442.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) >= 1, "At least 1 region must be detected on page 58442"
    price_region = next((r for r in resp.regions if "恐惧值" in r.text or "售价" in r.text), None)
    assert price_region is not None, "Price / Fear Value region must be detected"
    assert "1000000" in price_region.text, f"Number '1000000' must be preserved in text: {repr(price_region.text)}"
    assert "售价" in price_region.text, f"'售价' must be present in text: {repr(price_region.text)}"
    assert "恐惧值" in price_region.text, f"'恐惧值' must be present in text: {repr(price_region.text)}"
    assert price_region.text == "售价：\n1000000恐惧值", f"Expected exact text '售价：\\n1000000恐惧值', got {repr(price_region.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58444.png").exists(),
    reason="Page 58444 sample fixture not found",
)
def test_page_58444_ellipsis_trailing_segment_unified():
    """Page 58444 regression test:
    1. The trailing ellipsis dots of '只能换一颗土豆……' must be unified with the top bubble,
       NOT split into a rogue separate '1' region.
    2. All 3 speech bubbles on page 58444 must be detected cleanly as 3 separate regions.
    """
    img_path = FIXTURES_DIR / "page_58444.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 3, f"Expected exactly 3 dialogue regions on page 58444, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Bubble 1: Top speech bubble
    b1 = next((r for r in resp.regions if "恐惧值" in r.text or "土豆" in r.text), None)
    assert b1 is not None, "Top bubble must be detected"
    assert "一百万恐惧值" in b1.text, f"Line 1 missing in top bubble: {repr(b1.text)}"
    assert "只能换一颗土豆" in b1.text, f"Line 2 missing in top bubble: {repr(b1.text)}"

    # Ensure no rogue '1' region exists
    rogue_one = next((r for r in resp.regions if r.text.strip() == "1"), None)
    assert rogue_one is None, f"Found unexpected rogue '1' region: {rogue_one}"

    # Bubble 2: Middle speech bubble
    b2 = next((r for r in resp.regions if "顶级人物" in r.text or "需要一百万" in r.text), None)
    assert b2 is not None, "Middle bubble must be detected"
    assert "兑换一个顶级人物" in b2.text, f"Line 1 missing in middle bubble: {repr(b2.text)}"
    assert "需要一百万" in b2.text, f"Line 2 missing in middle bubble: {repr(b2.text)}"

    # Bubble 3: Bottom speech bubble
    b3 = next((r for r in resp.regions if "子龙" in r.text), None)
    assert b3 is not None, "Bottom bubble must be detected"
    assert "顶级子龙" in b3.text, f"Line 1 missing in bottom bubble: {repr(b3.text)}"
    assert "相当于一颗土豆" in b3.text, f"Line 2 missing in bottom bubble: {repr(b3.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58443.png").exists(),
    reason="Page 58443 sample fixture not found",
)
def test_page_58443_artwork_illustration_bypassed():
    """Page 58443 regression test:
    1. Giant background impact artwork/numbers (e.g. '1000000' stylized red numbers behind character)
       must be bypassed as illustration artwork and NOT mis-extracted as text/dialogue regions.
    2. Header watermarks (e.g. 'COLAMANGA .com', 'AcloudMerge.com') must be filtered out.
    3. The page contains no dialogue bubbles and should produce 0 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58443.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 0, f"Expected 0 dialogue regions on artwork page 58443, got {len(resp.regions)}: {[r.text for r in resp.regions]}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58544.png").exists(),
    reason="Page 58544 sample fixture not found",
)
def test_page_58544_stat_card_paragraph_unification():
    """Page 58544 regression test:
    1. Multi-line system notification stat cards must group into complete unified paragraphs.
    2. Card 2 ('嘟！获得顶级伐木工。\\n嘟！获得顶级伐...\\n嘟！获得顶级伐...') must be unified as 1 paragraph.
    3. Card 3 ('嘟！获得顶级伐……木工！\\n嘟！获得顶级女巫。\\n(附赠顶级宠物。)') must be unified as 1 paragraph.
    4. Sub-item / parenthetical lines like '(附赠顶级宠物。)' must not be orphaned into separate fragments.
    """
    img_path = FIXTURES_DIR / "page_58544.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # Card 2: Lumberjack multi-line card
    card2 = next((r for r in resp.regions if "伐木工" in r.text), None)
    assert card2 is not None, f"Card 2 (伐木工) must be detected. Found: {[r.text for r in resp.regions]}"
    assert "获得顶级伐木工" in card2.text, f"Line 1 missing from Card 2: {repr(card2.text)}"
    assert "获得顶级伐" in card2.text, f"Line 2/3 missing from Card 2: {repr(card2.text)}"

    # Card 4: Witch notification card
    card_witch = next((r for r in resp.regions if r.text.strip() == "嘟！获得顶级女巫。"), None)
    assert card_witch is not None, f"Card '嘟！获得顶级女巫。' must be its own separate region. Found: {[r.text for r in resp.regions]}"

    # Card 5: Bonus pet notification card
    card_pet = next((r for r in resp.regions if r.text.strip() == "(附赠顶级宠物。)"), None)
    assert card_pet is not None, f"Card '(附赠顶级宠物。)' must be its own separate region. Found: {[r.text for r in resp.regions]}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58536.png").exists(),
    reason="Page 58536 sample fixture not found",
)
def test_page_58536_watermark_collided_button_text_recovery():
    """Page 58536 regression test:
    1. '生活人才' button text colliding with orange/red 'ACloudMerge.com' watermark
       must be recovered cleanly as '生活人才' without watermark garbage.
    2. '点将：' label must remain separate from '生活人才' button (terminal colon guard).
    3. '战斗人才' button must be cleanly detected.
    4. Non-text icon drawings ('iii') must be suppressed.
    """
    img_path = FIXTURES_DIR / "page_58536.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # 1. 生活人才 button
    life_talent = next((r for r in resp.regions if "生活人才" in r.text), None)
    assert life_talent is not None, f"Button '生活人才' must be recovered cleanly. Found: {[r.text for r in resp.regions]}"
    assert "Merge" not in life_talent.text, f"Watermark domain must be stripped from '生活人才': {repr(life_talent.text)}"

    # 2. 点将： label
    summon_label = next((r for r in resp.regions if "点将" in r.text), None)
    assert summon_label is not None, f"Label '点将：' must be detected. Found: {[r.text for r in resp.regions]}"
    assert "生活" not in summon_label.text, f"'点将：' label must not be merged with '生活人才': {repr(summon_label.text)}"

    # 3. 战斗人才 button
    combat_talent = next((r for r in resp.regions if "战斗人才" in r.text), None)
    assert combat_talent is not None, f"Button '战斗人才' must be detected. Found: {[r.text for r in resp.regions]}"

    # 4. Ensure no rogue 'iii' icon drawing region exists
    rogue_icon = next((r for r in resp.regions if r.text.strip().lower() in ("iii", "888", "iin")), None)
    assert rogue_icon is None, f"Non-text icon drawing must be suppressed: {rogue_icon}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58515.png").exists(),
    reason="Page 58515 sample fixture not found",
)
def test_page_58515_single_bubble_exclamation_intact():
    """Page 58515 regression test:
    1. A single speech bubble containing mid-line exclamation marks ('啊啊啊啊！！！一想起来，\\n简直羞耻到爆啊！！')
       must remain a single unified paragraph and NOT get split into separate fragments.
    2. Page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58515.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58515, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Bubble 1: Top dialogue bubble
    b1 = next((r for r in resp.regions if "这种事" in r.text), None)
    assert b1 is not None, "Top bubble (这种事我才不要!) must be detected"

    # Bubble 2: Embarrassed scream bubble
    b2 = next((r for r in resp.regions if "羞耻到爆" in r.text), None)
    assert b2 is not None, "Embarrassed scream bubble must be detected"
    assert "啊啊啊啊！！！一想起来" in b2.text, f"Line 1 was severed or incomplete: {repr(b2.text)}"
    assert "简直羞耻到爆啊" in b2.text, f"Line 2 was missing: {repr(b2.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58509.png").exists(),
    reason="Page 58509 sample fixture not found",
)
def test_page_58509_watermark_bubble_unification():
    """Page 58509 regression test:
    1. A speech bubble colliding with an 'ACloudMerge.com' / 'COLAMANGA.com' watermark
       must have its 3 dialogue lines ('喂，你的手在抖，\\n咖啡都洒出来了，\\n怎么了？')
       unified into a single paragraph.
    2. Stray watermark text ('loudMer') must NOT become a dialogue region.
    """
    img_path = FIXTURES_DIR / "page_58509.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # 1. Top speech bubble
    top_bubble = next((r for r in resp.regions if "咖啡" in r.text), None)
    assert top_bubble is not None, f"Top coffee bubble must be detected. Found: {[r.text for r in resp.regions]}"
    assert "你的手在抖" in top_bubble.text, f"Line 1 missing from top bubble: {repr(top_bubble.text)}"
    assert "咖啡都洒出来了" in top_bubble.text, f"Line 2 missing from top bubble: {repr(top_bubble.text)}"
    assert "怎么了" in top_bubble.text, f"Line 3 missing from top bubble: {repr(top_bubble.text)}"

    # 2. Watermark must not be present as a region
    wm_reg = next((r for r in resp.regions if "loudmer" in r.text.lower() or "cloud" in r.text.lower()), None)
    assert wm_reg is None, f"Watermark 'loudMer' must be discarded: {wm_reg}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58539.png").exists(),
    reason="Page 58539 sample fixture not found",
)
def test_page_58539_stray_v_contour_suppressed():
    """Page 58539 regression test:
    1. A full art illustration page with 0 dialogue bubbles must have 0 dialogue regions.
    2. Stray non-Chinese 1-character artwork outline detections (e.g. 'V' contour near wrist)
       must be completely suppressed with 0 regions created.
    """
    img_path = FIXTURES_DIR / "page_58539.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 0, f"Expected 0 dialogue regions on page 58539, got {len(resp.regions)}: {[r.text for r in resp.regions]}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58520.png").exists(),
    reason="Page 58520 sample fixture not found",
)
def test_page_58520_separate_bubble_periods():
    """Page 58520 regression test:
    1. '好啦。' and '不说这些了。' sit in two distinct speech bubbles separated across panel boundaries.
       Because '好啦。' ends with a full-stop '。' and has a vertical gap, they must NOT be merged into
       a single paragraph.
    2. '听了这么多系统的事，\\n你现在有何感想？' inside a single bubble must remain a unified 2-line paragraph.
    """
    img_path = FIXTURES_DIR / "page_58520.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # 1. '好啦。' bubble
    b1 = next((r for r in resp.regions if r.text.strip() == "好啦。"), None)
    assert b1 is not None, f"'好啦。' must be its own separate region. Found: {[r.text for r in resp.regions]}"

    # 2. '不说这些了。' bubble
    b2 = next((r for r in resp.regions if r.text.strip() == "不说这些了。"), None)
    assert b2 is not None, f"'不说这些了。' must be its own separate region. Found: {[r.text for r in resp.regions]}"

    # 3. System question multi-line bubble
    b3 = next((r for r in resp.regions if "感想" in r.text), None)
    assert b3 is not None, f"System question bubble must be detected. Found: {[r.text for r in resp.regions]}"
    assert "听了这么多系统的事" in b3.text and "你现在有何感想" in b3.text, f"System question lines must be unified: {repr(b3.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58617.png").exists(),
    reason="Page 58617 sample fixture not found",
)
def test_page_58617_trailing_line_unification():
    """Page 58617 regression test:
    1. The top dialogue bubble contains 3 lines:
       '虽说婉儿当时性格刁蛮，\\n但妹妹甚至能艳压婉儿\\n一头，'
       The short 3rd line ('一头，') must be unified with the first two lines into 1 single paragraph.
    2. The bottom speech bubble ('如果有人能一亲...') must remain a clean unified multi-line paragraph.
    """
    img_path = FIXTURES_DIR / "page_58617.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58617, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Top speech bubble
    top_bubble = next((r for r in resp.regions if "婉儿" in r.text), None)
    assert top_bubble is not None, f"Top bubble must be detected. Found: {[r.text for r in resp.regions]}"
    assert "虽说婉儿当时性格刁蛮" in top_bubble.text, f"Line 1 missing: {repr(top_bubble.text)}"
    assert "但妹妹甚至能艳压婉儿" in top_bubble.text, f"Line 2 missing: {repr(top_bubble.text)}"
    assert "一头" in top_bubble.text, f"Line 3 ('一头，') must be unified with top bubble: {repr(top_bubble.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58623.png").exists(),
    reason="Page 58623 sample fixture not found",
)
def test_page_58623_ei_interjection_detection():
    """Page 58623 regression test:
    1. The small oval speech bubble at the bottom ('诶！') must be detected and preserved.
    2. '诶！' (which OCR rec models often omit due to vocabulary gaps) must not be dropped as pure punctuation.
    3. The upper bubbles ('樱姐姐。' and '云桃本就是无根之草...') must be cleanly detected.
    """
    img_path = FIXTURES_DIR / "page_58623.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 3, f"Expected 3 dialogue regions on page 58623, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # 1. '樱姐姐。'
    r0 = next((r for r in resp.regions if "樱姐姐" in r.text), None)
    assert r0 is not None, f"'樱姐姐。' missing. Found: {[r.text for r in resp.regions]}"

    # 2. '云桃本就是无根之草...'
    r1 = next((r for r in resp.regions if "云桃" in r.text), None)
    assert r1 is not None, f"'云桃本就是无根之草...' missing. Found: {[r.text for r in resp.regions]}"

    # 3. '诶！'
    r2 = next((r for r in resp.regions if "诶" in r.text or "！" in r.text and r.box.y > 1500), None)
    assert r2 is not None, f"'诶！' bubble at bottom must be detected. Found: {[r.text for r in resp.regions]}"
    assert "诶" in r2.text, f"'诶！' must contain '诶', got: {repr(r2.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58650.png").exists(),
    reason="Page 58650 sample fixture not found",
)
def test_page_58650_connected_bubble_exclamation_split():
    """Page 58650 regression test:
    1. The small top bubble '呼！' and the adjacent multi-line bubble '总算分完\\n最后一人\\n了。'
       must be detected as two separate dialogue regions, NOT merged into one single bubble.
    2. '多谢大人！\\n多谢大人！' inside the single bottom-left bubble must remain a unified 2-line region.
    3. The entire page must produce exactly 5 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58650.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 5, f"Expected exactly 5 dialogue regions on page 58650, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Region 1: Top crowd bubble
    r0 = next((r for r in resp.regions if "听明白了" in r.text), None)
    assert r0 is not None, f"Crowd bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "哦哦哦" in r0.text, f"'哦哦哦' missing in {repr(r0.text)}"

    # Region 2: Standalone '呼！' bubble
    r1 = next((r for r in resp.regions if r.text.strip() == "呼！"), None)
    assert r1 is not None, f"Standalone '呼！' bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "分完" not in r1.text, f"'呼！' must not contain '分完': {repr(r1.text)}"

    # Region 3: '总算分完\n最后一人\n了。' bubble
    r2 = next((r for r in resp.regions if "总算分完" in r.text), None)
    assert r2 is not None, f"'总算分完...' bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "呼" not in r2.text, f"'总算分完' bubble must not contain '呼': {repr(r2.text)}"
    assert "最后一人" in r2.text, f"'最后一人' missing in {repr(r2.text)}"
    assert "了" in r2.text, f"'了。' missing in {repr(r2.text)}"

    # Region 4: '多谢大人！\n多谢大人！' bubble
    r3 = next((r for r in resp.regions if "多谢大人" in r.text), None)
    assert r3 is not None, f"'多谢大人！' bubble missing. Found: {[r.text for r in resp.regions]}"
    assert r3.text.count("多谢大人") == 2, f"'多谢大人' should appear twice in unified bubble: {repr(r3.text)}"

    # Region 5: '最近好多流民\n加入啊！' bubble
    r4 = next((r for r in resp.regions if "流民" in r.text), None)
    assert r4 is not None, f"'最近好多流民...' bubble missing. Found: {[r.text for r in resp.regions]}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58876.png").exists(),
    reason="Page 58876 sample fixture not found",
)
def test_page_58876_watermark_bypass_and_bubble_unification():
    """Page 58876 regression test:
    1. Top speech bubble ('是啊，是啊！国师的道号\\n就叫“天赐”，他可是真正\\n救苦救难的大好人啊！')
       must be unified as exactly 1 dialogue region with 3 lines, NOT split mid-line or across lines.
    2. Bottom speech bubble ('天赐……啧！\\n说详细情况\\n吧。')
       must be unified as exactly 1 dialogue region with 3 lines.
    3. Watermarks ('COLAMANGA.com', 'AcleudMerge.com') must be bypassed and not emitted as dialogue.
    4. The page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58876.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58876, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Bubble 1: Top speech bubble (3 lines)
    b1 = next((r for r in resp.regions if "天赐" in r.text and "救苦救难" in r.text), None)
    assert b1 is not None, f"Top speech bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "是啊，是啊！国师的道号" in b1.text, f"Line 1 missing or fragmented in: {repr(b1.text)}"
    assert "就叫“天赐”，他可是真正" in b1.text, f"Line 2 missing in: {repr(b1.text)}"
    assert "救苦救难的大好人啊！" in b1.text, f"Line 3 missing in: {repr(b1.text)}"
    assert b1.text.count("\n") == 2, f"Expected exactly 3 lines (2 newlines) in top bubble, got {b1.text.count(chr(10))}: {repr(b1.text)}"

    # Bubble 2: Bottom speech bubble (3 lines)
    b2 = next((r for r in resp.regions if "详细情况" in r.text or "啧" in r.text), None)
    assert b2 is not None, f"Bottom speech bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "天赐……啧！" in b2.text, f"Line 1 missing in: {repr(b2.text)}"
    assert "说详细情况" in b2.text, f"Line 2 missing in: {repr(b2.text)}"
    assert "吧。" in b2.text, f"Line 3 missing in: {repr(b2.text)}"

    # Ensure no watermark dialogue regions
    wm = next((r for r in resp.regions if "cola" in r.text.lower() or "merge" in r.text.lower()), None)
    assert wm is None, f"Watermark detected as region: {wm}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58895.png").exists(),
    reason="Page 58895 sample fixture not found",
)
def test_page_58895_vertical_dotted_scream_bubble_unification():
    """Page 58895 regression test:
    1. The tall vertical speech bubble ('呜\\n……\\n啊\\n……') must be unified as 1 single vertical dialogue region
       covering the full vertical bubble span (h >= 600px), NOT split into two disconnected 1-character boxes ('呜', '啊').
    2. The top horizontal dialogue bubble ('嘎啊……啊……\\n啊……嘎……') must remain intact.
    3. The page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58895.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58895, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # Bubble 1: Top horizontal bubble
    b0 = next((r for r in resp.regions if "嘎" in r.text), None)
    assert b0 is not None, f"Top horizontal bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "嘎啊" in b0.text, f"Line 1 missing in: {repr(b0.text)}"

    # Bubble 2: Tall vertical scream bubble
    b1 = next((r for r in resp.regions if ("呜" in r.text or "鸣" in r.text) and "啊" in r.text), None)
    assert b1 is not None, f"Tall vertical scream bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b1.box.h >= 600, f"Vertical bubble height must span the full bubble (h >= 600), got {b1.box.h}"
    assert b1.vertical is True, f"Vertical bubble must have vertical=True, got {b1.vertical}"
    assert "……" in b1.text or "..." in b1.text, f"Interpolated vertical ellipsis dots missing from: {repr(b1.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58896.png").exists(),
    reason="Page 58896 sample fixture not found",
)
def test_page_58896_exclamation_mark_recovery():
    """Page 58896 regression test:
    1. The large exclamation scream bubble ('啊啊\\n啊啊\\n！啊\\n！啊\\n！') must completely scope
       the bottom exclamation mark and recover '！' rather than misreading it as '1'.
    2. Header watermarks ('COLAMANGA.com', 'AcloudMerge.com') must be bypassed.
    3. The page must produce exactly 1 dialogue/sfx region.
    """
    img_path = FIXTURES_DIR / "page_58896.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 1, f"Expected exactly 1 dialogue region on page 58896, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    reg = resp.regions[0]
    assert "1" not in reg.text, f"Misclassified '1' found in scream bubble: {repr(reg.text)}"
    assert reg.text.endswith("！") or reg.text.endswith("!"), f"Scream bubble must end in exclamation mark: {repr(reg.text)}"
    assert "啊啊" in reg.text, f"'啊啊' missing in: {repr(reg.text)}"
    assert reg.box.h >= 450, f"Region box height should cover the full scream bubble (h >= 450), got {reg.box.h}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58961.png").exists(),
    reason="Page 58961 sample fixture not found",
)
def test_page_58961_paragraph_angle_stability():
    """Page 58961 regression test:
    1. Standard horizontal speech bubbles ('呵呵，司马倩...') must maintain angle = 0.0,
       not falsely inherit a rotation angle from minor subpixel line jitter on trailing lines.
    2. Both dialogue bubbles must be horizontal (vertical=False, angle=0.0).
    3. The page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58961.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58961, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = next((r for r in resp.regions if "司马倩" in r.text), None)
    assert b0 is not None, f"Top dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b0.angle == 0.0, f"Top dialogue bubble must have angle 0.0, got {b0.angle}"
    assert b0.vertical is False

    b1 = next((r for r in resp.regions if "开心" in r.text and "司马倩" not in r.text), None)
    assert b1 is not None, f"Bottom dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b1.angle == 0.0, f"Bottom dialogue bubble must have angle 0.0, got {b1.angle}"
    assert b1.vertical is False


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58966.png").exists(),
    reason="Page 58966 sample fixture not found",
)
def test_page_58966_trailing_ellipsis_recovery():
    """Page 58966 regression test:
    1. The top speech bubble ('我比较特殊，仙人能\\n帮我消除部分影响，\\n而且她的位格更高……')
       must recover the trailing horizontal ellipsis ('……') that was omitted by OCR text line recognizers.
    2. The bottom speech bubble ('唔……') must remain intact.
    3. The page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58966.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58966, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = next((r for r in resp.regions if "位格" in r.text), None)
    assert b0 is not None, f"Top dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b0.text.endswith("……") or b0.text.endswith("..."), f"Trailing ellipsis missing from top bubble: {repr(b0.text)}"
    assert "我比较特殊" in b0.text

    b1 = next((r for r in resp.regions if "唔" in r.text), None)
    assert b1 is not None, f"Bottom dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "唔……" in b1.text or "唔..." in b1.text


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58969.png").exists(),
    reason="Page 58969 sample fixture not found",
)
def test_page_58969_bottom_ellipsis_line_recovery():
    """Page 58969 regression test:
    1. The top speech bubble ('你没杀过人，这很好，\\n那恨你的人应该比较少\\n……')
       must recover the bottom standalone ellipsis line ('……') below line 2.
    2. The bottom speech bubble ('嘿！这可不一定！\\n恨意和怒意大概\\n是有的！') must remain intact.
    3. The page must produce exactly 2 dialogue regions.
    """
    img_path = FIXTURES_DIR / "page_58969.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected exactly 2 dialogue regions on page 58969, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = next((r for r in resp.regions if "杀过人" in r.text), None)
    assert b0 is not None, f"Top dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b0.text.endswith("……") or b0.text.endswith("..."), f"Bottom ellipsis line missing from top bubble: {repr(b0.text)}"
    assert b0.text.count("\n") == 2, f"Expected 3 lines (2 newlines) in top bubble, got {b0.text.count(chr(10))}: {repr(b0.text)}"
    assert b0.box.h >= 130, f"Box height must cover the bottom ellipsis line (h >= 130), got {b0.box.h}"

    b1 = next((r for r in resp.regions if "这可不一定" in r.text), None)
    assert b1 is not None, f"Bottom dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "恨意和怒意" in b1.text


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58971.png").exists(),
    reason="Page 58971 sample fixture not found",
)
def test_page_58971_dialogue_angle_and_line_fragment_grouping():
    """Page 58971 regression test:
    1. Top dialogue bubble ('还有那些侠女的\\n花边新闻，嘿嘿\\n嘿~～～') must maintain angle = 0.0,
       and horizontal same-line fragments ('嘿' + '~～～') must group on the same line.
    2. Book title ('金瓶梅') on the tilted book cover retains its vertical orientation.
    3. The page must produce exactly 3 regions.
    """
    img_path = FIXTURES_DIR / "page_58971.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 3, f"Expected exactly 3 regions on page 58971, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = next((r for r in resp.regions if "侠女" in r.text), None)
    assert b0 is not None, f"Top dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b0.angle == 0.0, f"Top dialogue bubble must have angle 0.0, got {b0.angle}"
    assert b0.vertical is False
    assert "嘿~～～" in b0.text or "嘿~~~" in b0.text or "嘿～～～" in b0.text

    b1 = next((r for r in resp.regions if "金瓶梅" in r.text), None)
    assert b1 is not None, f"Book title missing. Found: {[r.text for r in resp.regions]}"
    assert b1.vertical is True

    b2 = next((r for r in resp.regions if "江湖上" in r.text), None)
    assert b2 is not None, f"Bottom dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert b2.angle == 0.0


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58994.png").exists(),
    reason="Page 58994 sample fixture not found",
)
def test_page_58994_short_trailing_line_angle_stability():
    """Page 58994 regression test:
    1. The dialogue bubble ('这是我第二次\\n来这里，上次\\n来还是在两年\\n前，') must maintain angle = 0.0,
       not rotate due to subpixel baseline jitter on the 1-character trailing line ('前，').
    2. The page must produce exactly 1 clean dialogue region.
    """
    img_path = FIXTURES_DIR / "page_58994.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 1, f"Expected exactly 1 dialogue region on page 58994, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = resp.regions[0]
    assert b0.angle == 0.0, f"Dialogue bubble must have angle 0.0, got {b0.angle}"
    assert b0.vertical is False
    assert "这是我第二次" in b0.text
    assert b0.text.endswith("前，") or b0.text.endswith("前,") or "前" in b0.text


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58966.png").exists(),
    reason="Page 58966 sample fixture not found",
)
def test_page_58966_trailing_ellipsis_coverage():
    """Page 58966 regression test:
    Top dialogue bubble ('我比较特殊，仙人能\\n帮我消除部分影响，\\n而且她的位格更高……') has a trailing
    ellipsis on line 3. The region polygon and box must expand rightward past x=830 to enclose all
    dots of the ellipsis, so inpainting completely removes the dots without leaving residue.
    """
    img_path = FIXTURES_DIR / "page_58966.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 2, f"Expected 2 regions on page 58966, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    b0 = next((r for r in resp.regions if "仙人能" in r.text), None)
    assert b0 is not None, f"Top dialogue bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "……" in b0.text or "..." in b0.text
    # Region must extend to at least x=830 to fully enclose all ellipsis dots
    assert b0.box.x + b0.box.w >= 830, f"Box width must cover the ellipsis (right >= 830): {b0.box}"
    assert max(p[0] for p in b0.polygon) >= 830, f"Polygon must cover the ellipsis: {b0.polygon}"

    # Verify inpainting leaves no dark residual dots on the white bubble background
    cleaned = pipeline.clean_image(img, [pipeline.CleanRequestRegion(id=r.id, box=r.box, polygon=r.polygon) for r in resp.regions])
    # The bubble interior at line 3 tail (x=750..830, y=290..360) must be pure white background
    gray = cv2.cvtColor(cleaned[290:360, 750:830], cv2.COLOR_BGR2GRAY)
    assert float(np.mean(gray)) > 250.0, f"Ellipsis dots not completely inpainted: mean={np.mean(gray)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_58976.png").exists(),
    reason="Page 58976 sample fixture not found",
)
def test_page_58976_flashback_scene_bubbles():
    """Page 58976 regression test:
    1. Flashback bubble ('生死爱恨... / 啊！真的太美了！ / 乱世！乱世呐！ / 黑暗的时刻就 / 要到来了！')
       must capture all 5 lines in Region 1 without dropping lines due to tight vertical spacing.
    2. The two distinct bottom-left oval speech bubbles:
       Top bubble ('不过……她\\n不重要。') and Bottom bubble ('我真正想找的人\\n……是你。')
       must NOT merge into one region, and each bubble must preserve both lines.
    """
    img_path = FIXTURES_DIR / "page_58976.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    # 1. Check flashback 5-line bubble
    flashback = next((r for r in resp.regions if "生死" in r.text or "生、死" in r.text or "乱世" in r.text), None)
    assert flashback is not None, f"Flashback bubble missing. Found: {[r.text for r in resp.regions]}"
    f_lines = [l.strip() for l in flashback.text.splitlines() if l.strip()]
    assert len(f_lines) == 5, f"Expected 5 lines in flashback bubble, got {len(f_lines)}: {f_lines}"
    assert "生、死、爱、恨" in f_lines[0] or "生死" in f_lines[0]
    assert "太美了" in f_lines[1]
    assert "乱世" in f_lines[2]
    assert "黑暗" in f_lines[3]
    assert "要到来了" in f_lines[4]

    # 2. Check bottom-left separate bubbles
    b_top = next((r for r in resp.regions if "不过" in r.text), None)
    assert b_top is not None, f"Top bottom-left bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "不重要" in b_top.text, f"Top bubble must include '不重要。': {repr(b_top.text)}"
    assert "我真正想找的人" not in b_top.text, f"Top bubble must NOT merge with bottom bubble: {repr(b_top.text)}"

    b_bot = next((r for r in resp.regions if "我真正想找的人" in r.text), None)
    assert b_bot is not None, f"Bottom bubble missing. Found: {[r.text for r in resp.regions]}"
    assert "是你" in b_bot.text, f"Bottom bubble must include '……是你。': {repr(b_bot.text)}"
    assert "不过" not in b_bot.text, f"Bottom bubble must NOT merge with top bubble: {repr(b_bot.text)}"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "page_63517.png").exists(),
    reason="Page 63517 sample fixture not found",
)
def test_page_63517_trailing_ellipsis_detection():
    """Page 63517 regression test:
    1. Region 0 ('龙字军夜袭“黑风寨”……') must recover the trailing horizontal ellipsis ('……')
       after the closing quote and expand bounding box/polygon past x=820 to enclose all 6 dots.
    2. Region 1 ('肥字军剿灭水贼\\n“混江龙”') must remain a clean 2-line dialogue region without
       erroneously inheriting a trailing ellipsis.
    3. Region 2 ('鱼字军剿灭……') must recover the trailing horizontal ellipsis ('……')
       and expand bounding box/polygon past x=850 to enclose all 6 dots.
    4. Exactly 3 regions must be produced.
    """
    img_path = FIXTURES_DIR / "page_63517.png"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    assert len(resp.regions) == 3, f"Expected exactly 3 regions on page 63517, got {len(resp.regions)}: {[r.text for r in resp.regions]}"

    # 1. Check dialogue 1 (Region 0)
    b0 = next((r for r in resp.regions if "龙字军" in r.text), None)
    assert b0 is not None, f"Dialogue 1 ('龙字军...') missing. Found: {[r.text for r in resp.regions]}"
    assert "黑风寨" in b0.text

    # 2. Check dialogue 2 (Region 1)
    b1 = next((r for r in resp.regions if "肥字军" in r.text), None)
    assert b1 is not None, f"Dialogue 2 ('肥字军...') missing. Found: {[r.text for r in resp.regions]}"
    assert "混江龙" in b1.text

    # 3. Check dialogue 3 (Region 2)
    b2 = next((r for r in resp.regions if "鱼字军" in r.text), None)
    assert b2 is not None, f"Dialogue 3 ('鱼字军...') missing. Found: {[r.text for r in resp.regions]}"
    assert "鱼字军剿灭" in b2.text


def test_page_45423_multiline_bubble_continuation_grouping():
    """Page 45423 test case:
    Line 1 ('因为是全息模拟，有') and the subsequent multi-line block
    ('些人将现实中的技巧\\n带入了游戏，这样他\\n们就比其他玩家有先\\n天的优势。')
    belong to the exact same speech bubble (same font size, overlapping X-range, tight vertical gap,
    no terminal punctuation after '有') and must group into ONE single paragraph instead of fragmenting.
    """
    # Box 1: Single line '因为是全息模拟，有' (x=371, y=796, w=293, h=37)
    b1 = np.array([[371, 796], [664, 796], [664, 833], [371, 833]], dtype=np.float64)
    txt1 = "因为是全息模拟，有"
    
    # Box 2: 4-line paragraph block (x=369, y=831, w=296, h=144)
    b2 = np.array([[369, 831], [665, 831], [665, 975], [369, 975]], dtype=np.float64)
    txt2 = "些人将现实中的技巧\n带入了游戏，这样他\n们就比其他玩家有先\n天的优势。"
    
    grouped, scores = detect.group_paragraphs([b1, b2], [0.99, 0.99], texts=[txt1, txt2])
    
    assert len(grouped) == 1, f"Expected exactly 1 merged paragraph box, got {len(grouped)}"
    gx, gy, gw, gh = detect.box_to_xywh(grouped[0])
    assert gx <= 371 and gy <= 796, f"Grouped box must enclose line 1: ({gx}, {gy}, {gw}, {gh})"
    assert gy + gh >= 975, f"Grouped box must enclose bottom lines: height={gh}, bottom={gy + gh}"


def test_page_45428_multiline_bubble_continuation_grouping():
    """Page 45428 test case:
    Line 1 ('而且我可是个自律') and the subsequent multi-line block
    ('的游戏工作者。隐\\n藏性质的东西我不\\n会去碰的！')
    belong to the exact same speech bubble (same font size, overlapping X-range, tight vertical gap,
    no terminal punctuation after '律') and must group into ONE single paragraph instead of fragmenting.
    """
    # Box 1: Single line '而且我可是个自律' (x=100, y=813, w=290, h=40)
    b1 = np.array([[100, 813], [390, 813], [390, 853], [100, 853]], dtype=np.float64)
    txt1 = "而且我可是个自律"
    
    # Box 2: 3-line paragraph block (x=99, y=851, w=292, h=124)
    b2 = np.array([[99, 851], [391, 851], [391, 975], [99, 975]], dtype=np.float64)
    txt2 = "的游戏工作者。隐\n藏性质的东西我不\n会去碰的！"
    
    grouped, scores = detect.group_paragraphs([b1, b2], [0.99, 0.99], texts=[txt1, txt2])
    
    assert len(grouped) == 1, f"Expected exactly 1 merged paragraph box, got {len(grouped)}"
    gx, gy, gw, gh = detect.box_to_xywh(grouped[0])
    assert gx <= 100 and gy <= 813, f"Grouped box must enclose line 1: ({gx}, {gy}, {gw}, {gh})"
    assert gy + gh >= 975, f"Grouped box must enclose bottom lines: height={gh}, bottom={gy + gh}"


def test_page_45360_bubble_separation_and_circle_tail_filtering():
    """Page 45360 test case:
    1. Top bubble ('靠！反正\\n最多挨顿\\n打，不过\\n是游戏，') and bottom bubble ('真是的，\\n自己又不\\n会受伤')
       are two distinct speech bubbles and must remain separate regions.
    2. The circular thought bubble tail ('000' / '°°°') must be discarded as circle noise and not hallucinate '诶……'.
    """
    top_lines = [
        (np.array([[213, 525], [363, 525], [363, 570], [213, 570]], dtype=np.float64), "靠！反正", 0.998),
        (np.array([[212, 565], [365, 565], [365, 613], [212, 613]], dtype=np.float64), "最多挨顿", 1.000),
        (np.array([[211, 604], [365, 604], [365, 652], [211, 652]], dtype=np.float64), "打，不过", 1.000),
        (np.array([[212, 644], [343, 644], [343, 693], [212, 693]], dtype=np.float64), "是游戏，", 1.000),
    ]
    bot_lines = [
        (np.array([[287, 720], [418, 720], [418, 769], [287, 769]], dtype=np.float64), "真是的，", 0.996),
        (np.array([[289, 762], [439, 762], [439, 808], [289, 808]], dtype=np.float64), "自己又不", 1.000),
        (np.array([[287, 801], [405, 801], [405, 850], [287, 850]], dtype=np.float64), "会受伤", 1.000),
    ]
    
    all_b = [l[0] for l in top_lines + bot_lines]
    all_s = [l[2] for l in top_lines + bot_lines]
    all_t = [l[1] for l in top_lines + bot_lines]
    
    grouped, _ = detect.group_paragraphs(all_b, all_s, texts=all_t)
    assert len(grouped) == 2, f"Expected exactly 2 distinct bubble regions, got {len(grouped)}"
    
    top_box = next((b for b in grouped if detect.box_to_xywh(b)[1] < 600), None)
    bot_box = next((b for b in grouped if detect.box_to_xywh(b)[1] >= 700), None)
    assert top_box is not None, "Top bubble box must be preserved"
    assert bot_box is not None, "Bottom bubble box must be preserved"
    
    # Verify circle noise pattern is rejected
    assert bool(pipeline.re.fullmatch(r'^[0oO·•\s]{1,6}$', "000"))
    assert not detect._CHINESE_RE.search("000")
























