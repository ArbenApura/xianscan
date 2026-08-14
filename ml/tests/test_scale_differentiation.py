# SCALE-DIFFERENTIATION & CROSS-BUBBLE SEPARATION TESTS
# TDD PHASE 1 (RED): These tests define the desired behaviour. They must all
# pass after the production fix is applied, and a subset must FAIL before it.
#
# Tests 1-5  → font-size-relative gap invariance (from implementation_plan.md)
# Test 6     → adjacent-bubble centroid drift guard (the page-678 regression)
from __future__ import annotations

import numpy as np
import pytest

from app.detect import box_to_xywh, group_paragraphs, merge_text_lines


def _box(x: int, y: int, w: int, h: int) -> np.ndarray:
    """Axis-aligned 4-point box (TL, TR, BR, BL)."""
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)


# ---------------------------------------------------------------------------
# §1  Font-size-relative gap invariance (Stage 2 of the implementation plan)
# ---------------------------------------------------------------------------


class TestScaleDifferentiation:
    """Verify that gap thresholds scale with font height (dimensionless k = gap/h).

    group_paragraphs uses gap_factor=0.45 which means:
      - gap ≤ 0.45 × min(h1, h2)  → SAME paragraph
      - gap >  0.45 × min(h1, h2) → SEPARATE paragraphs
    """

    # --- Whisper text (h=14px, k_lead=0.45 → max internal gap=6.3px) ---

    def test_whisper_lines_group_at_tight_gap(self):
        """h=14px, gap=5px < 0.45×14=6.3px → must group into one paragraph."""
        l1 = _box(80, 100, 120, 14)
        l2 = _box(80, 119, 120, 14)  # gap = 119 - (100+14) = 5
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 1, (
            f"Whisper lines with gap=5px must group; got {len(merged)} paragraphs"
        )

    def test_whisper_lines_separate_at_wide_gap(self):
        """h=14px, gap=9px > 0.45×14=6.3px → must be two separate paragraphs."""
        l1 = _box(80, 100, 120, 14)
        l2 = _box(80, 123, 120, 14)  # gap = 123 - (100+14) = 9
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 2, (
            f"Whisper lines with gap=9px must stay separate; got {len(merged)} paragraphs"
        )

    # --- Standard dialogue (h=26px, k_lead=0.45 → max internal gap=11.7px) ---

    def test_dialogue_lines_group_at_tight_gap(self):
        """h=26px, gap=10px < 0.45×26=11.7px → must group."""
        l1 = _box(100, 200, 200, 26)
        l2 = _box(100, 236, 200, 26)  # gap = 236 - (200+26) = 10
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 1

    def test_dialogue_lines_separate_at_gutter(self):
        """h=26px, gap=15px > 0.45×26=11.7px → must stay separate."""
        l1 = _box(100, 200, 200, 26)
        l2 = _box(100, 241, 200, 26)  # gap = 241 - (200+26) = 15
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 2

    # --- Shout / title text (h=70px, k_lead=0.45 → max internal gap=31.5px) ---

    def test_shout_lines_group_at_leading_gap(self):
        """h=70px, gap=28px < 0.45×70=31.5px → must group."""
        l1 = _box(50, 100, 400, 70)
        l2 = _box(50, 198, 400, 70)  # gap = 198 - (100+70) = 28
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 1

    def test_shout_lines_separate_at_gutter_gap(self):
        """h=70px, gap=40px > 0.45×70=31.5px → must stay separate."""
        l1 = _box(50, 100, 400, 70)
        l2 = _box(50, 210, 400, 70)  # gap = 210 - (100+70) = 40
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 2

    # --- Mixed font sizes ---

    def test_different_font_sizes_never_group(self):
        """h1=14px, h2=45px → ratio 45/14=3.21 > height_sim_max=1.50 → separate."""
        l1 = _box(80, 100, 120, 14)
        l2 = _box(80, 118, 120, 45)  # gap=4px — tiny gap but font sizes differ
        merged, _ = group_paragraphs([l1, l2], [0.9, 0.9])
        assert len(merged) == 2, (
            "Lines with h1=14px and h2=45px must never group (font-size gate)"
        )


# ---------------------------------------------------------------------------
# §2  X-centroid drift guard (the page-678 regression)
# ---------------------------------------------------------------------------


class TestSideBySideBubbles:
    """Two speech bubbles sitting at the same height in the same panel.

    They are side-by-side (different x-centers), not stacked. Their x-ranges
    partially overlap because the right bubble's left edge intrudes into the
    x-span of the left bubble's last line. The chaining-through-last-line
    logic in group_paragraphs naively merges them via that overlap.

    The x-centroid drift guard must prevent this.

    Geometry (mirrors page 678 panel 3):
      LEFT  bubble lines:  x=[82, 210],  cx=146,  h=32px, 4 lines y=[662..793]
      RIGHT bubble lines:  x=[170, 383], cx=276,  h=32px, 4 lines y=[760..892]

    Crucially: L4 (y=761-793) and R1 (y=760-792) OVERLAP vertically — they sit
    at the same height. The slight x-overlap (170..210 = 40px) passes the
    current 20% min-width threshold (20%×128=25.6px). But the centroid jump
    from cx=146 to cx=276.5 is 130.5px = 61% of max_w — above the 0.60 guard.
    """

    # Left bubble: 4 lines, all centered at cx≈146
    L1 = _box(82, 662, 128, 32)   # cx=146
    L2 = _box(82, 696, 128, 32)   # cx=146
    L3 = _box(82, 729, 128, 32)   # cx=146
    L4 = _box(82, 761, 128, 32)   # cx=146, bottom y=793

    # Right bubble: 4 lines, all centered at cx≈276
    R1 = _box(170, 760, 213, 32)  # cx=276.5, y-overlap with L4 (-33px gap)
    R2 = _box(170, 793, 213, 32)  # cx=276.5
    R3 = _box(170, 826, 213, 32)  # cx=276.5
    R4 = _box(170, 860, 213, 32)  # cx=276.5

    def test_adjacent_bubbles_not_merged_via_chaining(self):
        """THE PAGE-678 REGRESSION.

        8 OCR lines from two side-by-side bubbles must produce exactly 2
        paragraphs (one per bubble), not 1 merged super-region.

        This test FAILS on the current implementation (no centroid guard) and
        PASSES after the fix is applied.
        """
        all_boxes = [self.L1, self.L2, self.L3, self.L4,
                     self.R1, self.R2, self.R3, self.R4]
        all_scores = [0.99] * 8

        result, _ = group_paragraphs(all_boxes, all_scores)

        assert len(result) == 2, (
            f"Expected 2 separate paragraphs for side-by-side bubbles, got {len(result)}. "
            "The centroid drift guard in group_paragraphs must block cross-bubble chaining."
        )

        # Also verify each paragraph's approximate bounding box
        boxes_xywh = sorted([box_to_xywh(b) for b in result], key=lambda r: r[0])
        left_para,  right_para  = boxes_xywh[0], boxes_xywh[1]

        # Left paragraph should span x≈82, y=662..793 (union of L1-L4)
        assert left_para[0] == 82,  f"Left paragraph x should be 82, got {left_para[0]}"
        assert left_para[1] == 662, f"Left paragraph y should be 662, got {left_para[1]}"

        # Right paragraph should start at x=170
        assert right_para[0] == 170, f"Right paragraph x should be 170, got {right_para[0]}"

    def test_normal_centered_bubble_still_groups(self):
        """Regression guard: a normal multi-line bubble with lines of VARYING
        widths (but the same center) must still group into one paragraph.

        This verifies the centroid guard does NOT break normal grouping where
        shorter bottom lines are centered within a wider bubble.
        """
        # Wide first line, progressively narrower (all centered at cx=250)
        l1 = _box(100, 100, 300, 30)  # cx=250
        l2 = _box(120, 133, 260, 30)  # cx=250, gap=3px
        l3 = _box(140, 166, 220, 30)  # cx=250, gap=3px
        l4 = _box(160, 199, 180, 30)  # cx=250, gap=3px

        result, _ = group_paragraphs([l1, l2, l3, l4], [0.9] * 4)

        assert len(result) == 1, (
            f"4-line centered bubble must group into 1 paragraph, got {len(result)}"
        )

    def test_slight_x_offset_bubble_still_groups(self):
        """A bubble whose text is slightly left- or right-weighted (ragged right)
        must still group. The centroid can drift up to ~50% of line width without
        triggering the guard.

        Example: a ragged-right bubble where line centers shift by ~40px while
        lines are ~180px wide (drift ≈ 22% < 60%).
        """
        l1 = _box(100, 100, 180, 28)  # cx=190
        l2 = _box(100, 131, 160, 28)  # cx=180, gap=3
        l3 = _box(100, 162, 140, 28)  # cx=170, gap=3
        l4 = _box(100, 193, 120, 28)  # cx=160, gap=3

        result, _ = group_paragraphs([l1, l2, l3, l4], [0.9] * 4)

        assert len(result) == 1, (
            "Ragged-right bubble with moderate centroid drift must still group"
        )


# ---------------------------------------------------------------------------
# §3  merge_text_lines — suspicious x-overlap guard (page-678 regression)
# ---------------------------------------------------------------------------


class TestMergeTextLinesXOverlap:
    """merge_text_lines merges boxes that share a y-band (same horizontal row).
    When two boxes OVERLAP in x (gap < 0), a small overlap is normal OCR
    imprecision; a large overlap means two separate speech bubbles whose last/
    first lines happen to sit at the same height in the same panel.

    The rule:
      gap < -max(h, lh) * 0.30  AND  union_w > max(w, lw) * 1.20
      → suspicious: different speech bubbles, do NOT merge.

    The near-duplicate exception (union ≤ max_w * 1.20) lets two OCR backends
    that detected the SAME line with slightly different bounds still merge.

    Page-678 geometry (panel 3, third row):
      L4: x=[82, 210],  y=[761, 793],  cx=146  (last line of left bubble)
      R1: x=[184, 384], y=[760, 792],  cx=284  (first line of right bubble)
      gap_x = 184-210 = -26  (overlap!)
      overlap_px = 26 > 0.30*32=9.6 — suspicious
      union_w = 384-82=302 > max(128,200)*1.20=240 — not near-duplicate → REJECT
    """

    def test_does_not_merge_side_by_side_bubble_lines_at_same_height(self):
        """THE PAGE-678 MERGE_TEXT_LINES REGRESSION.

        L4 (last line of left bubble) and R1 (first line of right bubble)
        overlap in x by 26px and share a y-band. They must NOT be merged into
        one wide horizontal box.

        Before the fix: merge_text_lines fuses them into [82,384] cx=233,
        which then fools group_paragraphs into treating both bubbles as one.

        This test FAILS on the current code and PASSES after the fix.
        """
        L4 = _box(82,  761, 128, 32)   # x=[82,210],  cx=146
        R1 = _box(184, 760, 200, 32)   # x=[184,384], cx=284, gap=-26 with L4

        merged, _ = merge_text_lines([L4, R1], [0.99, 0.99])

        assert len(merged) == 2, (
            f"L4 and R1 are from different speech bubbles and must stay separate "
            f"after merge_text_lines; got {len(merged)} box(es). "
            f"The suspicious x-overlap guard must block this merge."
        )
        xs = sorted(box_to_xywh(b)[0] for b in merged)
        assert xs[0] == 82,  f"Left box must start at x=82,  got {xs[0]}"
        assert xs[1] == 184, f"Right box must start at x=184, got {xs[1]}"

    def test_near_duplicate_detections_still_merge(self):
        """Two OCR backends detecting the SAME line with slightly different bounds
        (large x-overlap but near-identical extents) must still merge.

        union_w = 285-95=190, max_w=max(180,190)=190, ratio=1.0 <= 1.20
        → near-duplicate → ALLOW merge.
        """
        box_a = _box(95,  100, 190, 30)   # x=[95,285],  cx=190
        box_b = _box(100, 100, 180, 30)   # x=[100,280], cx=190

        merged, _ = merge_text_lines([box_a, box_b], [0.95, 0.90])

        assert len(merged) == 1, (
            "Near-duplicate detections (same line, slightly different bounds) "
            f"must merge into 1 box; got {len(merged)}"
        )

    def test_small_x_overlap_still_merges(self):
        """A tiny x-overlap (OCR imprecision, <= 0.30*h) must still be allowed.

        gap=-5, max_h=32, 0.30*32=9.6. 5 <= 9.6 → not suspicious → ALLOW.
        """
        box_a = _box(100, 200, 150, 32)   # x=[100,250]
        box_b = _box(245, 200, 120, 32)   # x=[245,365], gap=245-250=-5

        merged, _ = merge_text_lines([box_a, box_b], [0.95, 0.90])

        assert len(merged) == 1, (
            "Small x-overlap (5px <= 0.30*32=9.6px) must still merge; "
            f"got {len(merged)} box(es)"
        )

    def test_does_not_merge_across_terminal_punctuation_gap(self):
        """When a box ends with terminal punctuation ('！', '。', '？') and has a non-negative gap,
        it must not merge with an adjacent bubble's line on the same row.
        """
        box_left = _box(49, 105, 204, 47)    # "裤子上不可！"
        box_right = _box(273, 105, 79, 47)   # "哈哈！"
        merged, _ = merge_text_lines(
            [box_left, box_right],
            [0.99, 0.96],
            texts=["裤子上不可！", "哈哈！"],
        )
        assert len(merged) == 2, (
            f"Left bubble ending with '！' must not merge with right bubble; got {len(merged)}"
        )

