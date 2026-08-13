import numpy as np
import pytest
from app import pipeline


def test_stitch_vertical_images():
    img1 = np.zeros((100, 200, 3), dtype=np.uint8)
    img2 = np.ones((150, 200, 3), dtype=np.uint8) * 255
    stitched = pipeline.stitch_vertical_images(img1, img2)
    assert stitched.shape == (250, 200, 3)


def test_stitch_vertical_images_mismatched_widths():
    img1 = np.zeros((100, 200, 3), dtype=np.uint8)
    img2 = np.ones((100, 400, 3), dtype=np.uint8) * 255
    stitched = pipeline.stitch_vertical_images(img1, img2)
    assert stitched.shape == (150, 200, 3)
