# MANU-TRANSLATOR ML SIDECAR — FASTAPI ENTRYPOINT.
#
#   uvicorn app.main:app --host 127.0.0.1 --port 8001
#
# ENDPOINTS:
#   GET  /health         → {status, detector, inpainter, ocr} (MODEL AVAILABILITY, NOT JUST ALIVE)
#   POST /pages/analyze  → multipart image → {width, height, backend, regions:[{id,box,polygon,
#                           category,text,confidence,vertical}]}
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

    # 1. ComicTextDetector ONNX
    try:
        if pipeline.detector is not None and pipeline.detector.available():
            pipeline.detector._load()
            status["detector"] = True
            logger.info("ComicTextDetector initialized.")
        else:
            status["detector"] = False
    except Exception as e:
        status["detector"] = False
        logger.warning("ComicTextDetector warmup skipped: %s", e)

    # 2. RapidOCR Engine
    try:
        from . import ocr

        ocr._get_engine()
        status["ocr"] = True
        logger.info("RapidOCR engine initialized.")
    except Exception as e:
        status["ocr"] = False
        logger.warning("RapidOCR warmup skipped: %s", e)

    # 3. LaMa Inpainter ONNX
    try:
        from . import inpaint

        if inpaint.config.LAMA_MODEL_PATH.exists():
            inpaint._get_lama()
            status["inpainter"] = True
            logger.info("LaMa inpainter initialized.")
        else:
            status["inpainter"] = False
    except Exception as e:
        status["inpainter"] = False
        logger.warning("LaMa inpainter warmup skipped: %s", e)

    return status


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """LIFESPAN HANDLER: IMMEDIATELY WARM UP MODELS AT APPLICATION STARTUP."""
    warmup_models()
    yield


app = FastAPI(title="manua-translator ML sidecar", version="0.1.0", lifespan=lifespan)

# SIZE CAP: A MANHUA PAGE IS ~2-8MB AS PNG/JPEG; REJECT ANYTHING ABSURD EARLY.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@app.get("/health")
def health() -> dict:
    from .inpaint import available_backend

    return {
        "status": "ok",
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
    result = pipeline.analyze_image(img)
    return result.model_dump()


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


@app.post("/pages/clean")
def clean(image: UploadFile = File(...), regions: str = Form(...)) -> Response:
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
        cleaned = pipeline.clean_image(img, parsed)
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
