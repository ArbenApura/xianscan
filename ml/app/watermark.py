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
