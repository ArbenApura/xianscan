import os
import sys
from pathlib import Path
import numpy as np
import pytest

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








