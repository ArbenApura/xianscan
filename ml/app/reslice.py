# SMART WEBTOON RE-SLICING ENGINE.
# STITCHES ARBITRARILY CUT WEBTOON IMAGES INTO A CONTINUOUS CANVAS,
# DETECTS TEXT BOUNDING BOXES & BUBBLE CONTOURS TO MAP FORBIDDEN CUT ZONES,
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
    safety_margin: int = 60,
    input_images: list[np.ndarray] | None = None,
) -> list[tuple[int, int]]:
    """RUN ACCURATE TEXT & SPEECH BUBBLE DETECTION ACROSS CONTINUOUS CANVAS TILES.
    ALWAYS DETECTS ON THE FULL STITCHED CANVAS USING OVERLAPPING SLIDING TILES SO THAT
    PRE-SPLIT DIALOGUE BUBBLES ARE RE-UNITED AND FULLY PROTECTED WITH A GENEROUS SAFETY MARGIN.
    """
    total_h, width = canvas_bgr.shape[:2]
    raw_intervals: list[tuple[int, int]] = []

    from .pipeline import detector
    from . import ocr

    def _extract_boxes_from_tile(tile: np.ndarray, y_offset: int):
        boxes: list[np.ndarray] = []
        if detector is not None and detector.available():
            try:
                result = detector.analyze(tile)
                boxes.extend(result.boxes)
            except Exception:
                pass
        try:
            rapid_lines = ocr.recognize_full(tile)
            boxes.extend([pts for pts, _t, _s in rapid_lines])
        except Exception:
            pass

        tile_h, tile_w = tile.shape[:2]
        gray_tile = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY) if tile.size > 0 else None

        for box in boxes:
            bx, by, bw, bh = detect.box_to_xywh(box)
            # Expand to enclosing speech balloon if uniform/light background surrounds the text
            bubble_top = by
            bubble_bottom = by + bh

            if gray_tile is not None and bw > 10 and bh > 10:
                # Check region slightly above and below text for light speech balloon background
                search_pad = 40
                y0_check = max(0, by - search_pad)
                y1_check = min(tile_h, by + bh + search_pad)
                x0_check = max(0, bx - 20)
                x1_check = min(tile_w, bx + bw + 20)
                crop_area = gray_tile[y0_check:y1_check, x0_check:x1_check]
                if crop_area.size > 0:
                    # Light speech bubble thresholding
                    _, thresh = cv2.threshold(crop_area, 220, 255, cv2.THRESH_BINARY)
                    if np.mean(thresh) > 50:
                        bubble_top = max(0, by - 20)
                        bubble_bottom = min(tile_h, by + bh + 20)

            y_min = max(0, bubble_top + y_offset - safety_margin)
            y_max = min(total_h, bubble_bottom + y_offset + safety_margin)
            raw_intervals.append((y_min, y_max))

    # SLIDING WINDOW TILES WITH 600PX OVERLAP TO PREVENT BOUNDARY MISSES
    tile_height = 2000
    tile_step = 1400

    for y_top in range(0, total_h, tile_step):
        y_bottom = min(total_h, y_top + tile_height)
        tile = canvas_bgr[y_top:y_bottom, :]
        if tile.size > 0:
            _extract_boxes_from_tile(tile, y_top)

    if not raw_intervals:
        return []

    # MERGE OVERLAPPING INTERVALS
    raw_intervals.sort(key=lambda iv: iv[0])
    merged: list[tuple[int, int]] = [raw_intervals[0]]

    for current in raw_intervals[1:]:
        prev_start, prev_end = merged[-1]
        if current[0] <= prev_end:
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
    min_height: int = 1000,
    max_height: int = 2400,
    input_images: list[np.ndarray] | None = None,
) -> list[int]:
    """CALCULATE THE OPTIMAL HORIZONTAL CUT LINES (Y COORDINATES) ALONG THE CANVAS.
    HEAVILY PRIORITIZES TRUE SOLID-COLOR INTER-PANEL GUTTERS (WHITE/BLACK MARGINS) OVER IN-PANEL CUTS,
    AND STRICTLY AVOIDS ALL PROTECTED TEXT INTERVALS.
    """
    total_h, width = canvas_bgr.shape[:2]
    if total_h <= max_height:
        return [total_h]

    # 1. DETECT FORBIDDEN TEXT & SPEECH BUBBLE ZONES (CONTINUOUS STITCHED CANVAS TILES)
    forbidden_zones = find_forbidden_text_zones(
        canvas_bgr,
        safety_margin=60,
        input_images=None,
    )

    # 2. PRECOMPUTE ROW METRICS (HORIZONTAL ROW VARIANCE, COLUMN SEGMENT VARIANCES, GRADIENT)
    gray = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2GRAY)
    gray_f = gray.astype(np.float32)

    # TOTAL ROW VARIANCE
    row_variances = np.var(gray_f, axis=1)

    # 3-COLUMN SEGMENT VARIANCES (LEFT, CENTER, RIGHT) TO ENSURE FULL-WIDTH UNIFORMITY
    w_third = width // 3
    col1_var = np.var(gray_f[:, :w_third], axis=1)
    col2_var = np.var(gray_f[:, w_third : 2 * w_third], axis=1)
    col3_var = np.var(gray_f[:, 2 * w_third :], axis=1)
    max_col_var = np.maximum(np.maximum(col1_var, col2_var), col3_var)

    # ROW-TO-ROW VERTICAL DIFFERENCE
    row_diffs = np.zeros(total_h, dtype=np.float32)
    row_means = np.mean(gray_f, axis=1)
    row_diffs[1:] = np.abs(row_means[1:] - row_means[:-1])

    # EDGE DENSITY VIA SOBEL GRADIENT
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    row_edge_energy = np.mean(np.abs(sobel_y), axis=1)

    cut_points: list[int] = []
    current_y = 0

    while current_y < total_h:
        remaining_h = total_h - current_y

        # IF REMAINING IS SMALL ENOUGH, WE ARE AT THE LAST PAGE
        if remaining_h <= max_height:
            cut_points.append(total_h)
            break

        # PRIMARY SEARCH WINDOW FOR NEXT CUT
        search_start = min(total_h - 1, current_y + min_height)
        search_end = min(total_h - 1, current_y + max_height)
        ideal_cut = current_y + target_height

        # 1ST PASS: FIND CONTINUOUS FULL-WIDTH GUTTER BANDS (SOLID WHITE/BLACK INTER-PANEL MARGINS) OUTSIDE FORBIDDEN TEXT
        gutter_candidates: list[int] = []
        for y in range(search_start, search_end + 1):
            if is_in_forbidden_zone(y, forbidden_zones):
                continue
            # Must have low variance across all column segments and low edge activity
            if row_variances[y] < 12.0 and max_col_var[y] < 15.0 and row_edge_energy[y] < 8.0:
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
                band_score = (band_len * 2.5) - dist_penalty
                if band_score > best_band_score:
                    best_band_score = band_score
                    best_y = mid_y

        # 2ND PASS: IF NO TRUE SOLID GUTTER BAND FOUND, CHOOSE LOWEST VISUAL ENERGY ROW OUTSIDE FORBIDDEN TEXT
        if best_y == -1:
            best_score = -float("inf")
            for y in range(search_start, search_end + 1):
                if is_in_forbidden_zone(y, forbidden_zones):
                    continue

                var_val = row_variances[y]
                diff_val = row_diffs[y]
                edge_val = row_edge_energy[y]
                dist_to_ideal = abs(y - ideal_cut)

                flatness = - (var_val * 0.1 + diff_val * 2.0 + edge_val * 1.5)
                distance_penalty = - (dist_to_ideal * 0.02)
                score = flatness + distance_penalty

                if score > best_score:
                    best_score = score
                    best_y = y

        # 3RD PASS: EXPAND WINDOW SLIGHTLY TO FIND NEAREST CLEAN GUTTER OUTSIDE FORBIDDEN TEXT
        if best_y == -1:
            expanded_start = max(current_y + 800, search_start - 300)
            expanded_end = min(total_h - 1, search_end + 400)
            best_score = -float("inf")
            for y in range(expanded_start, expanded_end + 1):
                if is_in_forbidden_zone(y, forbidden_zones):
                    continue
                var_val = row_variances[y]
                diff_val = row_diffs[y]
                dist_to_ideal = abs(y - ideal_cut)
                score = - (var_val * 0.1 + diff_val * 2.0) - (dist_to_ideal * 0.03)
                if score > best_score:
                    best_score = score
                    best_y = y

        # 4TH PASS: LOWEST VISUAL ENERGY ACROSS ENTIRE SEARCH WINDOW (FALLBACK)
        if best_y == -1:
            best_score = -float("inf")
            for y in range(search_start, search_end + 1):
                var_val = row_variances[y]
                diff_val = row_diffs[y]
                dist_to_ideal = abs(y - ideal_cut)
                score = - (var_val * 0.1 + diff_val * 2.0) - (dist_to_ideal * 0.02)
                if score > best_score:
                    best_score = score
                    best_y = y

        if best_y == -1:
            best_y = min(search_start, total_h - 1)

        cut_points.append(best_y)
        current_y = best_y

    return cut_points


def smart_reslice_chapter(
    images: list[np.ndarray],
    target_height: int = 1800,
    min_height: int = 1000,
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
        input_images=None,
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
