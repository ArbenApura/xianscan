# PYTHON WATERMARK REMOVER — STEP 0 PRE-PROCESSING ENGINE.
#
# RUNS DIRECTLY ON THE RAW IMAGE ARRAY BEFORE COMIC TEXT DETECTION & OCR.
# DETECTS & INPAINTS CORNER STAMPS, WATERMARK LOGOS, MARGIN BANNERS,
# AND SEMI-TRANSPARENT TEXT OVERLAYS.

from __future__ import annotations

import cv2
import numpy as np

from .inpaint import get_inpainter


class WatermarkRemover:
    """PRE-PROCESSES MANHUA PAGE IMAGES TO DETECT AND INPAINT WATERMARKS BEFORE OCR."""

    def __init__(self, inpainter_backend: str | None = None) -> None:
        self._inpainter_backend = inpainter_backend

    def create_watermark_mask(
        self,
        img_bgr: np.ndarray,
        corner_margin_pct: float = 0.08,
        detect_text_stamps: bool = True,
    ) -> np.ndarray:
        """GENERATE A BINARY MASK FOR WATERMARKS, LOGOS, AND CORNER STAMPS."""
        h, w = img_bgr.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)

        # 1. CORNER & MARGIN LOGO STAMP DETECTION (TOP & BOTTOM BORDER STAMPS)
        if corner_margin_pct > 0:
            margin_h = int(h * corner_margin_pct)
            margin_w = int(w * corner_margin_pct)

            # TOP-RIGHT CORNER (COMMON LOGO POSITION FOR MANHUA SITES)
            tr_crop = img_bgr[:margin_h, w - margin_w :]
            gray_tr = cv2.cvtColor(tr_crop, cv2.COLOR_BGR2GRAY)
            # HIGH-CONTRAST LOGO DETECT
            _, thresh_tr = cv2.threshold(gray_tr, 240, 255, cv2.THRESH_BINARY)
            if np.count_nonzero(thresh_tr) > 50:
                mask[:margin_h, w - margin_w :] = thresh_tr

            # BOTTOM MARGIN BANNER DETECT
            bm_crop = img_bgr[h - margin_h :, :]
            gray_bm = cv2.cvtColor(bm_crop, cv2.COLOR_BGR2GRAY)
            _, thresh_bm = cv2.threshold(gray_bm, 245, 255, cv2.THRESH_BINARY)
            if np.count_nonzero(thresh_bm) > 100:
                mask[h - margin_h :, :] = thresh_bm

        # 2. ADAPTIVE COLOR / SEMI-TRANSPARENT OVERLAY WATERMARK DETECTION
        if detect_text_stamps:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            # MORPHOLOGICAL TOP-HAT FILTER TO HIGHLIGHT FAINT WATERMARK OVERLAYS
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
            tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
            _, thresh = cv2.threshold(tophat, 40, 255, cv2.THRESH_BINARY)

            # FIND SMALL REPEATING WATERMARK CONTOURS
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                x, y, cw, ch = cv2.boundingRect(cnt)
                # FILTER WATERMARK STAMP BOUNDING BOXES (SMALL, THIN TEXT BANNERS NEAR EDGES)
                if (10 < area < 5000) and (x < w * 0.15 or x + cw > w * 0.85 or y < h * 0.08 or y + ch > h * 0.92):
                    cv2.drawContours(mask, [cnt], -1, 255, -1)

        # DILATE MASK SLIGHTLY TO COVER EDGES OF WATERMARK LOGOS
        dilation_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.dilate(mask, dilation_kernel, iterations=1)

        return mask

    def create_bubble_watermark_mask(
        self,
        img_bgr: np.ndarray,
        bubble_thresh: int = 220,
        min_sat: int = 25,
        min_val: int = 40,
        min_color_diff: int = 20,
    ) -> np.ndarray:
        """GENERATE A BINARY MASK FOR CHROMATIC WATERMARKS / LOGO OVERLAYS COLLIDING WITH WHITE SPEECH BUBBLES."""
        h, w = img_bgr.shape[:2]
        page_area = h * w

        # 1. IDENTIFY WHITE SPEECH BUBBLE CANDIDATES (PURE WHITE / NEAR-WHITE WITH LOW SATURATION)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]

        bright_mask = ((gray >= bubble_thresh) & (sat <= 30)).astype(np.uint8) * 255

        # FIND BUBBLE REGIONS (REJECT OVERSIZED BACKGROUNDS LIKE DESERTS/SKIES)
        bubble_mask = np.zeros((h, w), dtype=np.uint8)
        contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if 500 <= area <= max(120000, int(0.50 * page_area)):
                hull = cv2.convexHull(cnt)
                cv2.drawContours(bubble_mask, [hull], -1, 255, -1)

        bubble_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35))
        bubble_candidates = cv2.morphologyEx(bubble_mask, cv2.MORPH_CLOSE, bubble_kernel)

        # 2. DETECT CHROMATIC WATERMARK PIXELS
        b, g, r = img_bgr[:, :, 0], img_bgr[:, :, 1], img_bgr[:, :, 2]
        max_c = np.maximum(np.maximum(r, g), b)
        min_c = np.minimum(np.minimum(r, g), b)
        color_diff = max_c - min_c

        chromatic = ((sat >= min_sat) | (color_diff >= min_color_diff)) & (val >= min_val)
        colliding = (chromatic & (bubble_candidates > 0)).astype(np.uint8) * 255

        # 3. FILTER CONNECTED COMPONENTS (WATERMARK TEXT STROKES ONLY, NEVER CHARACTER ART)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(colliding, connectivity=8)
        mask = np.zeros((h, w), dtype=np.uint8)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            cw = stats[i, cv2.CC_STAT_WIDTH]
            ch = stats[i, cv2.CC_STAT_HEIGHT]
            if 8 <= area <= 10000 and (cw <= 400 or ch <= 150):
                mask[labels == i] = 255

        if np.any(mask):
            dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            mask = cv2.dilate(mask, dilate_kernel, iterations=1)

        return mask

    def remove_colliding_watermarks(
        self,
        img_bgr: np.ndarray,
        bubble_thresh: int = 220,
    ) -> tuple[np.ndarray, bool]:
        """FORCEFULLY INPAINT CHROMATIC WATERMARKS COLLIDING WITH SPEECH BUBBLES BEFORE OCR."""
        mask = self.create_bubble_watermark_mask(img_bgr, bubble_thresh=bubble_thresh)
        if np.count_nonzero(mask) < 30:
            return img_bgr, False

        # INPAINT CHROMATIC OVERLAY USING FAST-MARCHING / TELEA RESTORING LOCAL WHITE BUBBLE CONTEXT
        cleaned = cv2.inpaint(img_bgr, mask, 3, cv2.INPAINT_TELEA)
        return cleaned, True

    def process(
        self,
        img_bgr: np.ndarray,
        corner_margin_pct: float = 0.08,
        detect_stamps: bool = True,
    ) -> np.ndarray:
        """PRE-PROCESS IMAGE BY DETECTING & INPAINTING WATERMARKS."""
        mask = self.create_watermark_mask(
            img_bgr,
            corner_margin_pct=corner_margin_pct,
            detect_text_stamps=detect_stamps,
        )

        if not np.any(mask):
            return img_bgr

        inpainter = get_inpainter()
        if not inpainter.available():
            return img_bgr
        return inpainter(img_bgr, mask)


# DEFAULT SINGLETON INSTANCE
watermark_remover = WatermarkRemover()
