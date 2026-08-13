# SMART WEBTOON RE-SLICING ENGINE.
# STITCHES ARBITRARILY CUT WEBTOON IMAGES INTO A CONTINUOUS CANVAS,
# DETECTS TEXT BOUNDING BOXES TO MAP FORBIDDEN CUT ZONES,
# AND FINDS OPTIMAL PANEL GUTTERS (BLANK MARGINS) TO SLICE INTO CLEAN PAGES.

from __future__ import annotations

import cv2
import numpy as np

from . import detect


def stitch_images_vertically(images: list[np.ndarray]) -> np.ndarray:
    """STITCH A LIST OF BGR IMAGES VERTICALLY.
    NORMALIZES ALL IMAGES TO MATCH THE MAXIMUM WIDTH AMONG THEM."""
    if not images:
        raise ValueError("no images provided for stitching")
    if len(images) == 1:
        return images[0]

    max_w = max(img.shape[1] for img in images)
    resized_images: list[np.ndarray] = []

    for img in images:
        h, w = img.shape[:2]
        if w != max_w:
            new_h = max(1, int(h * (max_w / w)))
            resized = cv2.resize(img, (max_w, new_h), interpolation=cv2.INTER_AREA)
            resized_images.append(resized)
        else:
            resized_images.append(img)

    return np.vstack(resized_images)


def find_forbidden_text_zones(
    canvas_bgr: np.ndarray,
    safety_margin: int = 120,
    cluster_gap: int = 250,
) -> list[tuple[int, int]]:
    """RUN COMPREHENSIVE TEXT DETECTION (COMIC DETECTOR + RAPID OCR UNION) ON THE CONTINUOUS CANVAS.
    CLUSTERS ALL DIALOGUE AND CREDITS (WITHIN 250PX) WITH 120PX SAFETY MARGIN SO BLOCKS ARE NEVER SPLIT.
    """
    h, w = canvas_bgr.shape[:2]
    boxes: list[np.ndarray] = []

    # 1. COMIC BUBBLE DETECTOR
    try:
        from .pipeline import detector
        if detector is not None and detector.available():
            result = detector.analyze(canvas_bgr)
            boxes.extend(result.boxes)
    except Exception:
        pass

    # 2. RAPID OCR FOR CREDITS, PUBLISHER TEXT & SMALL ANNOTATIONS
    try:
        from . import ocr
        rapid_lines = ocr.recognize_full(canvas_bgr)
        boxes.extend([pts for pts, _t, _s in rapid_lines])
    except Exception:
        pass

    intervals: list[tuple[int, int]] = []

    for box in boxes:
        bx, by, bw, bh = detect.box_to_xywh(box)
        y_min = max(0, by - safety_margin)
        y_max = min(h, by + bh + safety_margin)
        intervals.append((y_min, y_max))

    if not intervals:
        return []

    # MERGE OVERLAPPING AND CLOSELY SPACED DIALOGUE/CREDIT BLOCKS (WITHIN cluster_gap)
    intervals.sort(key=lambda iv: iv[0])
    merged: list[tuple[int, int]] = [intervals[0]]

    for current in intervals[1:]:
        prev_start, prev_end = merged[-1]
        # IF CURRENT TEXT BLOCK OVERLAPS OR IS NEAR THE PREVIOUS BLOCK (WITHIN cluster_gap), MERGE INTO ONE PROTECTED SCENE BLOCK
        if current[0] <= prev_end + cluster_gap:
            merged[-1] = (prev_start, max(prev_end, current[1]))
        else:
            merged.append(current)

    return merged


def is_in_forbidden_zone(y: int, forbidden_zones: list[tuple[int, int]]) -> bool:
    """CHECK IF Y FALLS INSIDE ANY FORBIDDEN TEXT INTERVAL."""
    for start, end in forbidden_zones:
        if start <= y <= end:
            return True
        if start > y:
            break
    return False


def find_optimal_cut_points(
    canvas_bgr: np.ndarray,
    target_height: int = 1800,
    min_height: int = 800,
    max_height: int = 3200,
) -> list[int]:
    """CALCULATE THE OPTIMAL HORIZONTAL CUT LINES (Y COORDINATES) ALONG THE CANVAS.
    HEAVILY PRIORITIZES TRUE SOLID-COLOR INTER-PANEL GUTTERS (WHITE/BLACK MARGINS) OVER IN-PANEL CUTS.
    """
    total_h, width = canvas_bgr.shape[:2]
    if total_h <= max_height:
        return [total_h]

    # 1. DETECT FORBIDDEN TEXT & SPEECH BUBBLE ZONES (WITH 120PX OUTLINE PADDING & 250PX CLUSTER GAP)
    forbidden_zones = find_forbidden_text_zones(canvas_bgr, safety_margin=120, cluster_gap=250)

    # 2. PRECOMPUTE ROW METRICS (HORIZONTAL ROW VARIANCE & GRADIENT)
    gray = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2GRAY)
    # VARIANCE PER ROW (FLAT ROWS LIKE SOLID WHITE OR BLACK GUTTERS HAVE NEAR 0 VARIANCE)
    row_variances = np.var(gray.astype(np.float32), axis=1)

    # ROW-TO-ROW VERTICAL DIFFERENCE
    row_diffs = np.zeros(total_h, dtype=np.float32)
    row_diffs[1:] = np.abs(np.mean(gray[1:].astype(np.float32), axis=1) - np.mean(gray[:-1].astype(np.float32), axis=1))

    cut_points: list[int] = []
    current_y = 0

    while current_y < total_h:
        remaining_h = total_h - current_y

        # IF REMAINING IS SMALL ENOUGH, WE ARE AT THE LAST PAGE
        if remaining_h <= max_height:
            cut_points.append(total_h)
            break

        # SEARCH WINDOW FOR NEXT CUT
        search_start = min(total_h - 1, current_y + min_height)
        search_end = min(total_h - 1, current_y + max_height)
        ideal_cut = current_y + target_height

        # 1ST PASS: FIND CONTINUOUS GUTTER BANDS (SOLID WHITE/BLACK INTER-PANEL MARGINS)
        gutter_candidates: list[int] = []
        for y in range(search_start, search_end + 1):
            if is_in_forbidden_zone(y, forbidden_zones):
                continue
            if row_variances[y] < 8.0:
                gutter_candidates.append(y)

        best_y = -1

        if gutter_candidates:
            # GROUP INTO CONTIGUOUS BANDS AND PICK THE CENTER OF THE BEST BAND CLOSEST TO TARGET
            bands: list[list[int]] = []
            curr_band: list[int] = [gutter_candidates[0]]
            for gy in gutter_candidates[1:]:
                if gy == curr_band[-1] + 1:
                    curr_band.append(gy)
                else:
                    bands.append(curr_band)
                    curr_band = [gy]
            bands.append(curr_band)

            # SCORE EACH BAND BY THICKNESS AND PROXIMITY TO IDEAL CUT HEIGHT
            best_band_score = -float("inf")
            for band in bands:
                mid_y = band[len(band) // 2]
                band_len = len(band)
                dist_penalty = abs(mid_y - ideal_cut) * 0.05
                band_score = (band_len * 2.0) - dist_penalty
                if band_score > best_band_score:
                    best_band_score = band_score
                    best_y = mid_y

        # 2ND PASS: IF NO TRUE SOLID GUTTER BAND FOUND, CHOOSE LOWEST VISUAL ENERGY ROW
        if best_y == -1:
            best_score = -float("inf")
            for y in range(search_start, search_end + 1):
                if is_in_forbidden_zone(y, forbidden_zones):
                    continue

                var_val = row_variances[y]
                diff_val = row_diffs[y]
                dist_to_ideal = abs(y - ideal_cut)

                flatness = - (var_val * 0.1 + diff_val * 2.0)
                distance_penalty = - (dist_to_ideal * 0.02)
                score = flatness + distance_penalty

                if score > best_score:
                    best_score = score
                    best_y = y

        # FALLBACK (SAFETY ONLY)
        if best_y == -1:
            best_y = min(search_start, total_h - 1)

        cut_points.append(best_y)
        current_y = best_y

    return cut_points


def smart_reslice_chapter(
    images: list[np.ndarray],
    target_height: int = 1800,
    min_height: int = 1200,
    max_height: int = 2400,
) -> list[np.ndarray]:
    """STITCH CHAPTER SLICES AND RE-SLICE INTO CLEAN PAGES ALONG NATURAL NON-TEXT GUTTERS."""
    if not images:
        return []
    if len(images) == 1 and images[0].shape[0] <= max_height:
        return images

    canvas = stitch_images_vertically(images)
    cut_points = find_optimal_cut_points(
        canvas,
        target_height=target_height,
        min_height=min_height,
        max_height=max_height,
    )

    sliced_pages: list[np.ndarray] = []
    prev_y = 0

    for cut_y in cut_points:
        if cut_y <= prev_y:
            continue
        slice_crop = canvas[prev_y:cut_y, :]
        if slice_crop.size > 0:
            sliced_pages.append(slice_crop)
        prev_y = cut_y

    return sliced_pages
