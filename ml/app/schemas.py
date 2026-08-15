# API CONTRACT — THE PYDANTIC SCHEMAS BOTH ENDPOINTS AND THE TEST SUITE PIN DOWN.
#
# REGION BOXES ARE AXIS-ALIGNED {x, y, w, h} IN *ORIGINAL IMAGE PIXELS* (TOP-LEFT ORIGIN).
# POLYGONS ARE 4+ VERTEX LISTS [[x, y], ...] IN THE SAME COORDINATE SPACE — USED TO BUILD THE
# INPAINTING MASK SO TEXT ON ARTWORK (NOT JUST SOLID BUBBLES) IS ERASED PRECISELY.
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

class Box(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class Region(BaseModel):
    """ONE DETECTED TEXT REGION — THE CORE PAYLOAD THE WEB APP PERSISTS AND TRANSLATES."""

    id: str
    box: Box
    polygon: list[list[int]] = Field(default_factory=list)
    text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    vertical: bool = False
    angle: float = 0.0

    @field_validator("polygon")
    @classmethod
    def polygon_has_pairs(cls, v: list[list[int]]) -> list[list[int]]:
        for pt in v:
            if len(pt) != 2:
                raise ValueError("polygon points must be [x, y] pairs")
        return v


class AnalyzeResponse(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    regions: list[Region] = Field(default_factory=list)
    # WHICH DETECTOR BACKEND PRODUCED THE REGIONS (debugging aid)
    backend: Literal["comic-ctd", "rapidocr-fallback"] = "comic-ctd"


class CleanRequestRegion(BaseModel):
    """THE WEB APP ECHOES BACK THE REGIONS IT WANTS ERASED (POLYGONS PREFERRED, BOX FALLBACK)."""

    id: str
    box: Box
    polygon: list[list[int]] = Field(default_factory=list)

    @field_validator("polygon")
    @classmethod
    def polygon_has_pairs(cls, v: list[list[int]]) -> list[list[int]]:
        for pt in v:
            if len(pt) != 2:
                raise ValueError("polygon points must be [x, y] pairs")
        return v


class SeamCheckResponse(BaseModel):
    split_detected: bool
    overlap_ratio: float = 0.0

