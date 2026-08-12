# MANU-TRANSLATOR ML SIDECAR — FASTAPI ENTRYPOINT.
#
#   uvicorn app.main:app --host 127.0.0.1 --port 8001
#
# ENDPOINTS:
#   GET  /health         → {status, detector, inpainter, ocr} (MODEL AVAILABILITY, NOT JUST ALIVE)
#   POST /pages/analyze  → multipart image → {width, height, backend, regions:[{id,box,polygon,
#                           category,text,confidence,vertical}]}
#   POST /pages/clean    → multipart image + `regions` JSON field → PNG WITH ORIGINAL TEXT ERASED
from __future__ import annotations

import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import config, pipeline
from .schemas import CleanRequestRegion

app = FastAPI(title="manua-translator ML sidecar", version="0.1.0")

# SIZE CAP: A MANHUA PAGE IS ~2-8MB AS PNG/JPEG; REJECT ANYTHING ABSURD EARLY.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@app.get("/health")
def health() -> dict:
    from .inpaint import get_inpainter

    return {
        "status": "ok",
        "detector": "comic-ctd" if pipeline.detector.available() else "rapidocr-fallback",
        "inpainter": get_inpainter().backend,
        "ocr": "rapidocr",
        "models_dir": str(config.MODELS_DIR),
    }


@app.post("/pages/analyze")
async def analyze(image: UploadFile = File(...)) -> dict:
    data = await image.read()
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
async def preprocess(image: UploadFile = File(...)) -> Response:
    """STEP 0: PRE-PROCESS RAW IMAGE TO REMOVE WATERMARKS, CORNER STAMPS, AND LOGOS BEFORE OCR."""
    data = await image.read()
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
async def clean(image: UploadFile = File(...), regions: str = Form(...)) -> Response:
    data = await image.read()
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
    cleaned = pipeline.clean_image(img, parsed)
    return Response(content=pipeline.encode_png(cleaned), media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
