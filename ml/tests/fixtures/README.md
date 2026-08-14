# Test Fixtures & Regression Image Samples

This directory stores permanent, version-controlled image samples used by `pytest` integration and regression test suites in `ml/tests/`.

---

## 📁 Sample Inventory

| File | Resolution | Source Chapter/Page | Regression / Behavior Tested |
| :--- | :--- | :--- | :--- |
| [`page_679.jpg`](page_679.jpg) | 800 × 2400 | Page 679 | **Text Completeness**: Ensures multi-line dialogue (*"难道这么多年张予德都在成都和你们在一起？"*) is completely captured without being fragmented. |
| [`page_683.jpg`](page_683.jpg) | 800 × 2400 | Page 683 | **Adjacent Bubble Separation**: Ensures side-by-side bubbles on the same horizontal band (*"这傻子非得尿裤子上不可！"* vs *"哈哈！"*) are not merged across panels. |
| [`page_688.jpg`](page_688.jpg) | 800 × 2400 | Page 688 | **Narration Panel Preservation**: Ensures middle-right panel narration box (*"但是在光辉之城受到袭击的时候..."*) is preserved and not dropped by watermark filters. |
| [`page_1057.png`](page_1057.png) | 800 × 2284 | Chapter 16 / Page 1057 | **Panel-Bounded Punctuation**: Prevents distant bottom-right watermark stamps (*"漫客栈"*) from being swallowed as trailing punctuation into panel-2 bubbles across panels. |
| [`page_1062.png`](page_1062.png) | 800 × 2264 | Chapter 16 / Page 1062 | **Vertical Multi-Line Bubble Rescue**: Ensures compact vertical bubbles (*"又干\n掉一\n只！"*) are recognized as 3 distinct lines rather than misread into garbled 1-line text (*"对期"*). |
| [`page_1070.png`](page_1070.png) | 800 × 2264 | Chapter 17 / Page 1070 | **SFX / Monologue Bubble Separation**: Prevents adjacent floating SFX text (*"打量"*) on the same horizontal row from merging with speech bubble dialogue lines (*"我看，太无礼"*), keeping the monologue bubble unified. |

---

## 🛠️ How to Add New Test Case Images

When fixing a new detection, segmentation, inpainting, or OCR regression:

1. **Save the Sample Image**:
   * Copy the raw uploaded page into this directory with a clean, semantic name:
     ```
     ml/tests/fixtures/page_<id_or_topic>.<ext>
     ```
   * *Tip*: Ensure the image is optimized/clean so repository size remains lightweight.

2. **Reference the Fixture Portably in Tests**:
   * **Never hardcode machine-specific absolute paths** (e.g. `r"c:\Users\..."`).
   * Use relative paths anchored to the test file using `pathlib.Path`:
     ```python
     from pathlib import Path
     import pytest
     from app import pipeline

     FIXTURES_DIR = Path(__file__).parent / "fixtures"

     @pytest.mark.skipif(
         not (FIXTURES_DIR / "page_1057.png").exists(),
         reason="Page 1057 fixture not found",
     )
     def test_page_1057_bubble_separation():
         img_path = FIXTURES_DIR / "page_1057.png"
         with open(img_path, "rb") as f:
             img = pipeline.decode_image(f.read())
         resp = pipeline.analyze_image(img)
         ...
     ```

3. **Commit the Fixture to Git**:
   * Unlike `web/data/uploads/` (which is git-ignored runtime data), `ml/tests/fixtures/` is tracked by Git so that CI and other developers can immediately run the full test suite.
