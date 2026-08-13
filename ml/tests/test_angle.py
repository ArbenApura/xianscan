import numpy as np
import pytest
from app.detect import calculate_box_angle
from app.schemas import Box, Region


def test_horizontal_box_has_zero_angle():
    # Standard horizontal box [top-left, top-right, bottom-right, bottom-left]
    pts = np.array([[10, 10], [100, 10], [100, 40], [10, 40]])
    angle = calculate_box_angle(pts)
    assert angle == 0.0


def test_tilted_clockwise_positive_angle():
    # Tilted 45 degrees clockwise: dx=100, dy=100
    pts = np.array([[0, 0], [100, 100], [80, 120], [-20, 20]])
    angle = calculate_box_angle(pts)
    assert pytest.approx(angle, 0.1) == 45.0


def test_tilted_counter_clockwise_negative_angle():
    # Tilted -30 degrees (upwards right): dx=100, dy=-57.7
    pts = np.array([[0, 100], [100, 100 - 57.7], [120, 120 - 57.7], [20, 120]])
    angle = calculate_box_angle(pts)
    assert pytest.approx(angle, 0.5) == -30.0


def test_small_angle_jitter_snapped_to_zero():
    # Tiny 1.0 degree tilt should snap to 0.0 to keep horizontal text crisp
    rad = np.radians(1.0)
    pts = np.array([[0, 0], [100 * np.cos(rad), 100 * np.sin(rad)], [100 * np.cos(rad), 30], [0, 30]])
    angle = calculate_box_angle(pts)
    assert angle == 0.0


def test_region_schema_default_angle():
    r = Region(id="r0", box=Box(x=0, y=0, w=50, h=20), polygon=[[0, 0], [50, 0], [50, 20], [0, 20]])
    assert r.angle == 0.0


def test_pipeline_computes_angle_from_matched_ocr_lines(monkeypatch):
    from app import pipeline
    from app.detect import DetectResult

    class FakeDetector:
        def available(self):
            return True

        def analyze(self, img):
            # Axis-aligned box covering the two angled lines
            box = np.array([[300, 950], [700, 950], [700, 1200], [300, 1200]])
            return DetectResult(boxes=[box], scores=[0.95], mask=np.zeros(img.shape[:2], dtype=np.uint8), backend="comic-ctd")

    line1 = np.array([[352.0, 994.0], [641.0, 1040.0], [633.0, 1094.0], [343.0, 1048.0]])
    line2 = np.array([[345.0, 1050.0], [674.0, 1105.0], [665.0, 1159.0], [336.0, 1104.0]])

    monkeypatch.setattr(pipeline, "detector", FakeDetector())
    monkeypatch.setattr(pipeline.ocr, "recognize_full", lambda img: [
        (line1, "【顶级人物十名。】", 0.99),
        (line2, "(附带一头顶级宠物)", 0.99),
    ])

    dummy_img = np.zeros((1600, 900, 3), dtype=np.uint8)
    res = pipeline.analyze_image(dummy_img)
    assert len(res.regions) == 1
    assert pytest.approx(res.regions[0].angle, 0.5) == 9.2

