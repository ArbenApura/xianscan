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

4. **Mandatory Full Test Suite Verification:**
   - Always run the full test suite (`pytest` in `ml/tests/`) against all existing real-page fixtures (`page_679.jpg`, `page_683.jpg`, `page_688.jpg`, title-subtitle separation tests, scale differentiation tests) to mathematically prove 0 regressions on standard story pages before committing any changes.
