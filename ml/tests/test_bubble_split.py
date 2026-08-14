import os
import sys
import numpy as np
import pytest

from app import detect, ocr, pipeline


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
    not os.path.exists(r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\11\e8216aa5-5510-4139-947c-b7147a78a339.jpg"),
    reason="Page 683 sample image not found",
)
def test_page_683_full_pipeline_bubble_separation():
    """Page 683 regression test: Left bubble ('这傻子非得尿\\n裤子上不可！') and right bubble ('哈哈！')
    must be detected as two distinct dialogue regions, NOT merged into one.
    """
    img_path = r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\11\e8216aa5-5510-4139-947c-b7147a78a339.jpg"
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
    not os.path.exists(r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\11\637f2a8e-69cf-405e-beff-4b3f0a8428c0.jpg"),
    reason="Page 679 sample image not found",
)
def test_page_679_full_pipeline_text_completeness():
    """Page 679 regression test: First bubble must contain full text '难道这么多年张予德都在成都和你们在一起？'
    and must not be fragmented or chopped.
    """
    img_path = r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\11\637f2a8e-69cf-405e-beff-4b3f0a8428c0.jpg"
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
    not os.path.exists(r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\12\09d545de-5c68-478e-8e66-b48c25ff6271.jpg"),
    reason="Page 688 sample image not found",
)
def test_page_688_narration_panel_detected():
    """Page 688 regression test: Middle-right panel narration text
    '但是在光辉之城受到袭击的时候，神圣世家却背叛了光辉之城，弃城而逃。'
    must be detected as a region and not erased by watermark filters.
    """
    img_path = r"c:\Users\Admin\Desktop\manua-translator\web\data\uploads\12\09d545de-5c68-478e-8e66-b48c25ff6271.jpg"
    with open(img_path, "rb") as f:
        img = pipeline.decode_image(f.read())

    resp = pipeline.analyze_image(img)

    narration = next((r for r in resp.regions if ("光辉之城" in r.text or "神圣世家" in r.text) and "背叛" in r.text), None)
    assert narration is not None, "Middle-right narration panel must be detected"
    assert "受到袭击" in narration.text, f"Narration text must include '受到袭击': {narration.text}"
    assert "背叛了" in narration.text, f"Narration text must include '背叛了': {narration.text}"
    assert "弃城而逃" in narration.text, f"Narration text must include '弃城而逃': {narration.text}"

