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







