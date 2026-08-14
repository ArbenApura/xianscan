# SMART RE-SLICE TESTS — VERIFIES STITCHING, GUTTER DETECTION, BUBBLE GUARD, AND CLEAN RESLICING.

import cv2
import numpy as np
from app.reslice import (
    find_forbidden_text_zones,
    find_optimal_cut_points,
    is_in_forbidden_zone,
    smart_reslice_chapter,
    stitch_images_vertically,
)
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_stitch_images_vertically():
    img1 = np.zeros((100, 200, 3), dtype=np.uint8)
    img2 = np.ones((150, 200, 3), dtype=np.uint8) * 255
    stitched = stitch_images_vertically([img1, img2])
    assert stitched.shape == (250, 200, 3)


def test_stitch_images_mismatched_width():
    img1 = np.zeros((100, 200, 3), dtype=np.uint8)
    img2 = np.zeros((100, 400, 3), dtype=np.uint8)
    stitched = stitch_images_vertically([img1, img2])
    assert stitched.shape[1] == 400
    assert stitched.shape[0] == 300


def test_find_optimal_cut_points_blank_gutters():
    # CREATE A 3000PX TALL CANVAS WITH ARTWORK (NOISE/TEXTURE) AND A CLEAN WHITE GUTTER AT Y=1750..1850
    np.random.seed(42)
    canvas = np.random.randint(50, 200, (3000, 400, 3), dtype=np.uint8)
    # INSERT A FLAT SOLID WHITE INTER-PANEL GUTTER AT Y=1750..1850
    canvas[1750:1850, :] = 255

    cuts = find_optimal_cut_points(canvas, target_height=1800, min_height=1200, max_height=2400)
    assert len(cuts) >= 2
    assert 1750 <= cuts[0] <= 1850
    assert cuts[-1] == 3000


def test_forbidden_zone_check():
    zones = [(100, 200), (500, 600)]
    assert is_in_forbidden_zone(150, zones) is True
    assert is_in_forbidden_zone(100, zones) is True
    assert is_in_forbidden_zone(200, zones) is True
    assert is_in_forbidden_zone(300, zones) is False
    assert is_in_forbidden_zone(550, zones) is True
    assert is_in_forbidden_zone(700, zones) is False


def test_smart_reslice_chapter():
    # 4 SLICES OF 800PX EACH = 3200PX TOTAL WITH CONTENT
    np.random.seed(42)
    slice1 = np.random.randint(0, 255, (800, 300, 3), dtype=np.uint8)
    slice2 = np.random.randint(0, 255, (800, 300, 3), dtype=np.uint8)
    slice3 = np.random.randint(0, 255, (800, 300, 3), dtype=np.uint8)
    slice4 = np.random.randint(0, 255, (800, 300, 3), dtype=np.uint8)

    # INSERT WHITE GUTTERS AT TARGET INTERVALS
    slice2[700:800, :] = 255

    pages = smart_reslice_chapter([slice1, slice2, slice3, slice4], target_height=1600, min_height=1000, max_height=2200)
    assert len(pages) >= 2
    total_h = sum(p.shape[0] for p in pages)
    assert total_h == 3200


def test_reslice_endpoint():
    # TEST POST /pages/reslice VIA FASTAPI TESTCLIENT
    _, png1 = cv2.imencode(".png", np.zeros((500, 300, 3), dtype=np.uint8))
    _, png2 = cv2.imencode(".png", np.zeros((500, 300, 3), dtype=np.uint8))

    files = [
        ("files", ("p1.png", png1.tobytes(), "image/png")),
        ("files", ("p2.png", png2.tobytes(), "image/png")),
    ]

    resp = client.post("/pages/reslice", files=files)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert int(resp.headers.get("X-Slice-Count", "0")) >= 1
