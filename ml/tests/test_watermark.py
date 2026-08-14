# WATERMARK PRE-PROCESSING TESTS — VERIFY WATERMARK MASK GENERATION AND INPAINTING.
from __future__ import annotations

import numpy as np
import pytest

from app.watermark import WatermarkRemover, watermark_remover


def test_watermark_remover_empty_image():
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    processed = watermark_remover.process(img)
    assert processed.shape == img.shape
    # An image with no corner stamps or overlays should remain unchanged
    np.testing.assert_array_equal(processed, img)


def test_create_watermark_mask_corner_logo():
    remover = WatermarkRemover()
    img = np.full((500, 500, 3), 100, dtype=np.uint8)
    # Simulate high-contrast logo in top-right corner
    img[:30, 460:] = 250
    mask = remover.create_watermark_mask(img, corner_margin_pct=0.08)
    assert mask.shape == (500, 500)
    assert mask[:30, 460:].any()


def test_create_watermark_mask_disabled():
    remover = WatermarkRemover()
    img = np.full((100, 100, 3), 250, dtype=np.uint8)
    mask = remover.create_watermark_mask(img, corner_margin_pct=0.0, detect_text_stamps=False)
    assert not mask.any()


def test_create_bubble_watermark_mask_detects_colored_overlay_in_bubble():
    remover = WatermarkRemover()
    # Create an image with a white speech bubble
    img = np.full((300, 300, 3), 50, dtype=np.uint8)  # dark background
    img[50:250, 50:250] = 255  # white speech bubble

    # Add black text stroke in the bubble
    img[120:140, 80:220] = 0  # black text (R=0, G=0, B=0)

    # Add colored watermark overlay (e.g. teal and red letters) across the bubble
    # Teal: B=150, G=150, R=50; Red: B=40, G=40, R=180
    img[90:110, 70:150] = [150, 150, 50]
    img[90:110, 150:230] = [40, 40, 180]

    mask = remover.create_bubble_watermark_mask(img)
    assert mask.shape == (300, 300)
    # Watermark region in the bubble must be masked
    assert mask[95, 100] == 255
    assert mask[95, 180] == 255
    # Black text must NOT be masked
    assert mask[130, 150] == 0
    # Clean white bubble area outside watermark must NOT be masked
    assert mask[200, 200] == 0


def test_remove_colliding_watermarks_inpaints_colored_overlay():
    remover = WatermarkRemover()
    img = np.full((300, 300, 3), 40, dtype=np.uint8)
    img[50:250, 50:250] = 255  # white bubble

    # Black text
    img[130:150, 80:220] = 0

    # Colored watermark
    img[80:110, 70:230] = [30, 30, 200]  # Red watermark letters

    cleaned, has_collision = remover.remove_colliding_watermarks(img)
    assert has_collision is True
    assert cleaned.shape == img.shape

    # The watermark area should be inpainted closer to white bubble background (high value in all channels)
    assert cleaned[95, 150, 0] > 180
    assert cleaned[95, 150, 1] > 180
    assert cleaned[95, 150, 2] > 180

    # Black text should remain dark
    assert cleaned[140, 150, 0] < 50
    assert cleaned[140, 150, 1] < 50
    assert cleaned[140, 150, 2] < 50

