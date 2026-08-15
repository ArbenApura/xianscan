# MANU-TRANSLATOR ML SIDECAR — FASTAPI ENTRYPOINT.
#
#   uvicorn app.main:app --host 127.0.0.1 --port 8001
#
# ENDPOINTS:
#   GET  /health         → {status, detector, inpainter, ocr} (MODEL AVAILABILITY, NOT JUST ALIVE)
#   POST /pages/analyze  → multipart image → {width, height, backend, regions:[{id,box,polygon,
#                           text,confidence,vertical}]}"}
#   POST /pages/clean    → multipart image + `regions` JSON field → PNG WITH ORIGINAL TEXT ERASED
from contextlib import asynccontextmanager
import json
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import config, pipeline
from .schemas import CleanRequestRegion

logger = logging.getLogger("app.main")


def warmup_models() -> dict[str, bool]:
    """PRE-INITIALIZE AND WARM UP DETECTOR, OCR, AND INPAINTER MODELS ON STARTUP."""
    status: dict[str, bool] = {}

    print("=" * 64)
    print("  XIANSCAN ML SIDECAR -- HARDWARE ACCELERATION ENGINE")
    print("=" * 64)
    print(f"  * Device / Accelerator : {config.DEVICE_LABEL}")
    print(f"  * Execution Providers  : {', '.join(config.ORT_PROVIDERS)}")
    print(f"  * Models Directory     : {config.MODELS_DIR}")
    print("-" * 64)

    # 1. ComicTextDetector ONNX
    try:
        if pipeline.detector is not None and pipeline.detector.available():
            pipeline.detector._load()
            status["detector"] = True
            print("  [+] ComicTextDetector    : Ready (ONNX DBNet)")
        else:
            status["detector"] = False
            print("  [-] ComicTextDetector    : Not Found (Fallback to RapidOCR Det)")
    except Exception as e:
        status["detector"] = False
        print(f"  [!] ComicTextDetector    : Error ({e})")

    # 2. RapidOCR Engine
    try:
        from . import ocr

        ocr._get_engine()
        status["ocr"] = True
        print("  [+] RapidOCR Engine      : Ready (PP-OCR / ONNX)")
    except Exception as e:
        status["ocr"] = False
        print(f"  [!] RapidOCR Engine      : Error ({e})")

    # 3. LaMa Inpainter ONNX
    try:
        from . import inpaint

        if inpaint.config.LAMA_MODEL_PATH.exists():
            inpaint._get_lama()
            status["inpainter"] = True
            print("  [+] LaMa Inpainter       : Ready (LaMa-Manga Dynamic FP32)")
        else:
            status["inpainter"] = False
            print("  [-] LaMa Inpainter       : Model File Not Found (Solid Infill Fallback)")
    except Exception as e:
        status["inpainter"] = False
        print(f"  [!] LaMa Inpainter       : Error ({e})")

    print("=" * 64)
    return status


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """LIFESPAN HANDLER: IMMEDIATELY WARM UP MODELS AT APPLICATION STARTUP."""
    warmup_models()
    yield


app = FastAPI(title="xianscan ML sidecar", version="0.1.0", lifespan=lifespan)

# SIZE CAP: A MANHUA PAGE IS ~2-8MB AS PNG/JPEG; REJECT ANYTHING ABSURD EARLY.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@app.get("/health")
def health() -> dict:
    from .inpaint import available_backend

    return {
        "status": "ok",
        "accelerator": config.DEVICE_LABEL,
        "providers": config.ORT_PROVIDERS,
        "detector": "comic-ctd" if pipeline.detector.available() else "rapidocr-fallback",
        "inpainter": available_backend(),
        "ocr": "rapidocr",
        "models_dir": str(config.MODELS_DIR),
    }


@app.post("/pages/analyze")
def analyze(image: UploadFile = File(...)) -> dict:
    # SYNC `def` (NOT `async def`) — FASTAPI RUNS IT IN THE THREADPOOL SO CONCURRENT REQUESTS FROM
    # THE WEB PIPELINE EXECUTE IN PARALLEL INSTEAD OF BLOCKING THE EVENT LOOP SERIALLY.
    data = image.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"image too large (>{MAX_UPLOAD_BYTES // (1 << 20)}MB)")
    try:
        img = pipeline.decode_image(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        result = pipeline.analyze_image(img)
        return result.model_dump()
    except Exception as e:
        logger.exception("Error analyzing image: %s. Using robust fallback OCR.", e)
        try:
            from . import detect, ocr

            page_h, page_w = img.shape[:2]
            rapid_lines = ocr.recognize_full(img)
            fallback_regions = []
            for idx, (pts, t, score) in enumerate(rapid_lines):
                x, y, w, h = detect.box_to_xywh(pts)
                if w > 0 and h > 0 and t.strip():
                    fallback_regions.append(
                        pipeline.Region(
                            id=f"r{idx}",
                            box=pipeline.Box(x=x, y=y, w=w, h=h),
                            polygon=[[int(p[0]), int(p[1])] for p in pts],
                            text=t.strip(),
                            confidence=float(score),
                            vertical=detect.is_vertical_box(pts),
                            angle=detect.calculate_box_angle(pts),
                        )
                    )
            return pipeline.AnalyzeResponse(
                width=page_w,
                height=page_h,
                regions=fallback_regions,
                backend="rapidocr-fallback",
            ).model_dump()
        except Exception as e2:
            logger.exception("Direct fallback OCR also failed: %s", e2)
            page_h, page_w = img.shape[:2] if img is not None else (1000, 800)
            return pipeline.AnalyzeResponse(
                width=page_w,
                height=page_h,
                regions=[],
                backend="rapidocr-fallback",
            ).model_dump()



@app.post("/pages/preprocess")
def preprocess(image: UploadFile = File(...)) -> Response:
    """STEP 0: PRE-PROCESS RAW IMAGE TO REMOVE WATERMARKS, CORNER STAMPS, AND LOGOS BEFORE OCR."""
    data = image.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"image too large (>{MAX_UPLOAD_BYTES // (1 << 20)}MB)")
    try:
        img = pipeline.decode_image(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    preprocessed = pipeline.preprocess_watermark(img)
    return Response(content=pipeline.encode_png(preprocessed), media_type="image/png")


@app.get("/system/hardware")
def get_hardware() -> dict:
    """RETURNS CURRENT COMPUTE ACCELERATOR AND HARDWARE DIAGNOSTICS."""
    from . import device

    return device.get_hardware_status()


@app.post("/system/device")
def set_device(payload: dict) -> dict:
    """DYNAMICALLY SWITCHES THE ACTIVE COMPUTE PROVIDER."""
    from . import device

    requested_device = payload.get("device", "auto")
    device.set_active_provider(requested_device)
    return device.get_hardware_status()


@app.post("/pages/clean")
def clean(
    image: UploadFile = File(...),
    regions: str = Form(...),
    inpaint_mode: str = Form("patch"),
) -> Response:
    data = image.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"image too large (>{MAX_UPLOAD_BYTES // (1 << 20)}MB)")
    try:
        parsed = [CleanRequestRegion.model_validate(r) for r in json.loads(regions)]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid regions JSON: {e}") from e
    try:
        img = pipeline.decode_image(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        cleaned = pipeline.clean_image(img, parsed, mode=inpaint_mode)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(content=pipeline.encode_png(cleaned), media_type="image/png")


@app.post("/pages/stitch")
def stitch(image_top: UploadFile = File(...), image_bottom: UploadFile = File(...)) -> Response:
    data_top = image_top.file.read()
    data_bot = image_bottom.file.read()
    if not data_top or not data_bot:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        img_top = pipeline.decode_image(data_top)
        img_bot = pipeline.decode_image(data_bot)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    stitched = pipeline.stitch_vertical_images(img_top, img_bot)
    return Response(content=pipeline.encode_png(stitched), media_type="image/png")


@app.post("/pages/reslice")
def reslice_pages(files: list[UploadFile] = File(...)) -> Response:
    """STITCH MULTIPLE WEBTOON SLICES AND RE-SLICE AT NATURAL NON-TEXT GUTTERS."""
    import io
    import zipfile
    from . import reslice

    if not files:
        raise HTTPException(status_code=400, detail="no files provided")

    images = []
    for f in files:
        data = f.file.read()
        if not data:
            continue
        try:
            img = pipeline.decode_image(data)
            images.append(img)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    if not images:
        raise HTTPException(status_code=400, detail="no valid images provided")

    sliced = reslice.smart_reslice_chapter(images)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, page in enumerate(sliced):
            png_bytes = pipeline.encode_png(page)
            zf.writestr(f"{idx}.png", png_bytes)

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"X-Slice-Count": str(len(sliced))},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
