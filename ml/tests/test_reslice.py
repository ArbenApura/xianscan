# SMART RE-SLICE TESTS — VERIFIES STITCHING, GUTTER DETECTION, AND CLEAN RESLICING.

import cv2
import numpy as np
from app.reslice import find_optimal_cut_points, smart_reslice_chapter, stitch_images_vertically
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
    # CREATE A 3000PX TALL IMAGE WITH A WHITE GUTTER AT Y=1800
    canvas = np.ones((3000, 400, 3), dtype=np.uint8) * 128
    # INSERT A FLAT WHITE GUTTER NEAR TARGET (y=1750 to 1850)
    canvas[1750:1850, :] = 255
    cuts = find_optimal_cut_points(canvas, target_height=1800, min_height=1200, max_height=2400)
    assert len(cuts) >= 2
    assert 1750 <= cuts[0] <= 1850
    assert cuts[-1] == 3000


def test_smart_reslice_chapter():
    # 4 SLICES OF 800PX EACH = 3200PX TOTAL
    slice1 = np.ones((800, 300, 3), dtype=np.uint8) * 50
    slice2 = np.ones((800, 300, 3), dtype=np.uint8) * 100
    slice3 = np.ones((800, 300, 3), dtype=np.uint8) * 150
    slice4 = np.ones((800, 300, 3), dtype=np.uint8) * 200

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
