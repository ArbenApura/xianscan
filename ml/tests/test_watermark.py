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
