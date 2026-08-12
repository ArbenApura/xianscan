# SCHEMA TESTS — THE API CONTRACT IS THE INTERFACE THE WEB APP DEPENDS ON; PIN IT.
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AnalyzeResponse, Box, CleanRequestRegion, Region


class TestBox:
    def test_valid(self):
        assert Box(x=1, y=2, w=100, h=50).model_dump() == {"x": 1, "y": 2, "w": 100, "h": 50}

    @pytest.mark.parametrize("bad", [{"x": -1, "y": 0, "w": 10, "h": 10}, {"x": 0, "y": 0, "w": 0, "h": 10}, {"x": 0, "y": 0, "w": 10, "h": -5}])
    def test_rejects_negative_or_empty_dims(self, bad):
        with pytest.raises(ValidationError):
            Box(**bad)


class TestRegion:
    def test_valid(self):
        r = Region(id="r0", box=Box(x=0, y=0, w=10, h=10), polygon=[[0, 0], [10, 0], [10, 10], [0, 10]])
        assert r.category == "other"  # DEFAULT
        assert r.vertical is False

    def test_rejects_bad_polygon_points(self):
        with pytest.raises(ValidationError):
            Region(id="r0", box=Box(x=0, y=0, w=10, h=10), polygon=[[0, 0, 0]])

    def test_rejects_unknown_category(self):
        with pytest.raises(ValidationError):
            Region(id="r0", box=Box(x=0, y=0, w=10, h=10), category="shout")

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            Region(id="r0", box=Box(x=0, y=0, w=10, h=10), confidence=1.5)


class TestCleanRequestRegion:
    def test_polygon_optional(self):
        r = CleanRequestRegion(id="r0", box=Box(x=1, y=2, w=3, h=4))
        assert r.polygon == []  # CALLER FALLS BACK TO THE BOX

    def test_rejects_bad_polygon(self):
        with pytest.raises(ValidationError):
            CleanRequestRegion(id="r0", box=Box(x=1, y=2, w=3, h=4), polygon=[[1]])


class TestAnalyzeResponse:
    def test_roundtrip(self):
        r = Region(id="r0", box=Box(x=0, y=0, w=10, h=10), text="你好", confidence=0.9, vertical=True)
        payload = AnalyzeResponse(width=800, height=1200, regions=[r], backend="comic-ctd").model_dump()
        assert payload["regions"][0]["text"] == "你好"
        assert payload["regions"][0]["vertical"] is True

    def test_rejects_zero_dimensions(self):
        with pytest.raises(ValidationError):
            AnalyzeResponse(width=0, height=1200, regions=[])
