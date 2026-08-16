# SYNTHETIC PAGE FIXTURES — DETERMINISTIC FAKE MANHUA PAGES SO THE WHOLE SUITE RUNS WITHOUT MODELS.
#
# THE "PAGE" IS A WHITE CANVAS WITH TWO DARK TEXT BLOBS (A BUBBLE-LIKE BLOCK AND A WIDE SFX BLOCK) —
# GOOD ENOUGH FOR MASK/CROP/INPAINT MATH AND FOR THE FAKE DETECTOR TESTS TO EXERCISE REAL GEOMETRY.
from __future__ import annotations

import os
import numpy as np
import pytest

from tests import cache_utils

PAGE_W, PAGE_H = 800, 1200


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--no-model-cache",
        action="store_true",
        default=False,
        help="Disable model inference cache and run live ONNX models",
    )
    parser.addoption(
        "--refresh-model-cache",
        action="store_true",
        default=False,
        help="Force re-computation and overwrite existing model inference cache entries",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--no-model-cache"):
        os.environ["PYTEST_NO_MODEL_CACHE"] = "1"
    if config.getoption("--refresh-model-cache"):
        os.environ["PYTEST_REFRESH_MODEL_CACHE"] = "1"

    # Patch detector, OCR, and inpainter with content-addressed cache
    cache_utils.patch_all_models()


def make_synthetic_page() -> np.ndarray:
    """BGR PAGE: WHITE BG, DARK 'DIALOGUE' BLOB TOP-LEFT, DARK 'SFX' BLOB BOTTOM-RIGHT."""
    img = np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)
    # DIALOGUE-LIKE REGION (SMALL, TALLISH → NOT SFX BY SIZE)
    img[150:230, 100:420] = (40, 40, 40)
    # SFX-LIKE REGION (WIDE **AND** VERY TALL → SFX BY THE CLASSIFIER RULE)
    img[860:1140, 50:750] = (20, 20, 20)
    return img


# CANONICAL EXPECTATIONS — THE FAKE DETECTOR RETURNS BOXES AT EXACTLY THESE COORDS.
DIALOGUE_BOX = np.array([[100, 150], [420, 150], [420, 230], [100, 230]], dtype=np.float64)
# SFX: WIDE **AND** VERY TALL (w=700 > 0.45×800, h=280 > 0.2×1200) — THE CLASSIFIER REQUIRES BOTH
SFX_BOX = np.array([[50, 860], [750, 860], [750, 1140], [50, 1140]], dtype=np.float64)


@pytest.fixture
def synthetic_page() -> np.ndarray:
	return make_synthetic_page()


def make_text_page() -> np.ndarray:
	"""PAGE WITH REAL (PIL-RENDERED) CJK TEXT — THE SMOKE TEST'S INPUT. THE SOLID-BLOCK PAGE ABOVE
	IS ONLY GOOD FOR GEOMETRY MATH; THE REAL DETECTOR NEEDS ACTUAL TEXT-LIKE STRUCTURES.
	INCLUDES A 2-LINE BUBBLE (STACKED, CENTER-ALIGNED) TO EXERCISE PARAGRAPH GROUPING."""
	import cv2
	from PIL import Image, ImageDraw, ImageFont

	img = Image.new("RGB", (800, 1000), "white")
	d = ImageDraw.Draw(img)
	font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 48)
	d.text((100, 100), "你好，世界！", fill="black", font=font)
	d.text((100, 300), "系统提示：开始翻译", fill="black", font=font)
	# THE 2-LINE BUBBLE — LINES ARE STACKED AND CENTER-ALIGNED (ONE PARAGRAPH EXPECTED)
	d.text((200, 520), "这是第一行", fill="black", font=font)
	d.text((230, 590), "这是第二行", fill="black", font=font)
	d.text((500, 700), "轰！", fill="black", font=font)
	return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


@pytest.fixture
def text_page() -> np.ndarray:
	return make_text_page()


@pytest.fixture
def page_png() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", make_synthetic_page())
    assert ok
    return buf.tobytes()
