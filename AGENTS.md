# Manhua Translator - Engineering Guidelines & Persistent Memory

## Critical Rule: Zero-Regression Sample Troubleshooting & Pipeline Optimization

When troubleshooting specific edge-case samples (e.g. staff credit pages, title splashes, logos, system stat cards, sound effects):

1. **NEVER Tweak Global Geometric Thresholds For Specific Edge-Cases:**
   - **Do NOT** blindly modify global thresholds in `merge_text_lines()`, `group_paragraphs()`, `_split_lines_by_internal_punctuation()`, or `deduplicate_boxes()` without context guards.
   - Example danger: Tightening vertical gap thresholds globally to fix a credit list (`承制\n分镜\n线稿\n上色`) will fragment ordinary 3-4 line dialogue speech bubbles across regular story pages.
   - Example danger: Banning horizontal line merges globally to prevent `总监制 阿布` from fusing will prevent legitimate single-sentence dialogue halves from merging.

2. **Always Use Context-Aware / Layout-Gated Differentiation:**
   - Implement specialized layout modes (e.g. `Credit / Table Grid Mode`, `Title / Splash Mode`, `Logo Classifier`) that only activate when structural signatures or keywords (`STAFF`, `原作`, `责编`, `监制`, `出品`, `制作`, regular column/row grids) are detected.
   - Regular comic panels and standard speech bubbles must remain on the proven narrative dialogue pipeline path.

3. **Logo & Brand Asset Protection:**
   - Detect and classify platform and studio logos (`腾讯动漫`, `阅文集团`, `快看`, etc.) as dedicated `LOGO` / `BRAND` entities.
   - Avoid routing graphic emblems through standard dialogue OCR and destructive inpainting.

4. **Mandatory Full Test Suite Verification (When Touching ML Files):**
   - Run the full test suite (`pytest` in `ml/tests/`) against all existing real-page fixtures (`page_679.jpg`, `page_683.jpg`, `page_688.jpg`, title-subtitle separation tests, scale differentiation tests) to prove 0 regressions on story pages whenever changes are made to Python/ML code.

5. **Skip Python Tests If No Python Files Touched:**
   - Do **NOT** execute Python/ML tests (`pytest`) if Python files in `ml/` were not modified, as the test suite is extensive and takes significant time. Only run `web` tests (`npm run test`) for web/frontend/TS changes.

6. **Pytest Model Inference Cache Awareness:**
   - The ML test suite uses a content-addressed inference cache in `ml/tests/.cache/` (`cache_utils.py`) for raw ONNX model outputs (`ComicTextDetector`, `RapidOCR`, `LamaInpainter`) to keep full pytest runs under 10 seconds.
   - Downstream Python heuristics (`group_paragraphs`, `merge_text_lines`, `_split_lines_by_internal_punctuation`, angle math) **always execute live**.
   - When adding new test image fixtures or troubleshooting raw model perception issues, use `--refresh-model-cache` to force re-inference and update the cache, or `--no-model-cache` to bypass the cache completely.

## Core Architectural Insights & Robustness Rules

1. **Strict Regex Gating for Single-Glyph / Punctuation OCR Fallbacks:**
   - RapidOCR's internal detector (`use_det=True`) regularly fails on compact single-glyph crops ($w \le 45\text{px}$ or $h \le 45\text{px}$, e.g. standalone vertical `！` or `？`).
   - Direct recognition (`use_det=False` / `recognize_line`) can recognize them, but **will hallucinate characters** (e.g. misinterpreting ellipsis dots as `'1'`, or texture as Chinese characters) if fed background noise.
   - **Rule:** Any direct recognition fallback on failed crops must be strictly gated by `_PUNCT_ONLY = re.compile(r"^[.．…·!！?？~～]{1,2}$")` to avoid rogue OCR detections.

2. **Empty Detector Boxes Must Not Inflate Text Line Heights:**
   - Detector boxes without OCR text (padding or trailing boxes) merged horizontally with genuine OCR lines must **never** expand vertical line bounds $[y_0, y_1]$.
   - Expanding vertical bounds inflates line height, breaking downstream font-similarity ratios ($h_1 / h_2 > 1.4$) in `group_paragraphs()` and fragmenting multi-line bubbles into separate regions.
   - **Rule:** Empty detector boxes may only widen horizontal span $[x_0, x_1]$, preserving the genuine text's vertical height $[y_0, y_1]$.

3. **Color Watermark Mask State Independence:**
   - `create_bubble_watermark_mask()` must **always** run on `img_bgr` (the raw original image), never on `ocr_img` (which has already been cleaned and has its chromatic pixels zeroed out).
   - Calling mask generators on cleaned images produces empty masks ($0$ pixels), causing downstream watermark filters to be silently bypassed.

4. **Font-Size Ratio Tolerance for Same-Bubble Pairs:**
   - Standard comic typesetting can have font scale variations up to $1.75\times$ within the same bubble (e.g. bold line followed by subtitle).
   - When vertical gap is small ($\le 0.35 \times \text{line height}$) and horizontal overlap is high ($\ge 50\%$), allow `height_ratio` up to $1.75$ in `group_paragraphs()`.


