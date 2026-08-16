# TEST MODEL INFERENCE CACHE UTILITY
# Caches raw invariant outputs of ComicTextDetector, RapidOCR, and LamaInpainter
# to accelerate pytest runs on iGPU/CPU from minutes to seconds.
from __future__ import annotations

import functools
import hashlib
import os
import pickle
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(__file__).parent / ".cache"


def _ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _hash_array(arr: Any) -> str:
    import numpy as np

    if not isinstance(arr, np.ndarray):
        return hashlib.sha256(str(arr).encode("utf-8")).hexdigest()
    contiguous = np.ascontiguousarray(arr)
    # Include shape & dtype in hash to avoid collision between reshaped arrays
    meta = f"{contiguous.shape}_{contiguous.dtype}".encode("utf-8")
    return hashlib.sha256(meta + contiguous.tobytes()).hexdigest()


def _get_cache_path(category: str, key: str) -> Path:
    return _ensure_cache_dir() / f"{category}_{key}.pkl"


def _read_cache(category: str, key: str) -> Any | None:
    path = _get_cache_path(category, key)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _write_cache(category: str, key: str, value: Any) -> None:
    path = _get_cache_path(category, key)
    try:
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass


def is_cache_disabled() -> bool:
    return os.environ.get("PYTEST_NO_MODEL_CACHE", "").lower() in ("1", "true", "yes")


def is_refresh_enabled() -> bool:
    return os.environ.get("PYTEST_REFRESH_MODEL_CACHE", "").lower() in ("1", "true", "yes")


# --- WRAPPERS --- #


def wrap_detector_analyze(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(self, img_bgr, *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(self, img_bgr, *args, **kwargs)

        h = _hash_array(img_bgr)
        if not is_refresh_enabled():
            cached = _read_cache("ctd", h)
            if cached is not None:
                return cached

        res = orig_fn(self, img_bgr, *args, **kwargs)
        _write_cache("ctd", h, res)
        return res

    return wrapper


def wrap_ocr_run_engine(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(img_bgr, use_det: bool = True, *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(img_bgr, use_det=use_det, *args, **kwargs)

        h = f"{_hash_array(img_bgr)}_det_{use_det}"
        if not is_refresh_enabled():
            cached = _read_cache("ocr_engine", h)
            if cached is not None:
                return cached

        res = orig_fn(img_bgr, use_det=use_det, *args, **kwargs)
        _write_cache("ocr_engine", h, res)
        return res

    return wrapper


def wrap_ocr_recognize_full(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(img_bgr, tiled: bool = True, *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(img_bgr, tiled=tiled, *args, **kwargs)

        h = f"{_hash_array(img_bgr)}_tiled_{tiled}"
        if not is_refresh_enabled():
            cached = _read_cache("ocr_full", h)
            if cached is not None:
                return cached

        res = orig_fn(img_bgr, tiled=tiled, *args, **kwargs)
        _write_cache("ocr_full", h, res)
        return res

    return wrapper


def wrap_ocr_recognize_crop(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(img_bgr, *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(img_bgr, *args, **kwargs)

        h = _hash_array(img_bgr)
        if not is_refresh_enabled():
            cached = _read_cache("ocr_crop", h)
            if cached is not None:
                return cached

        res = orig_fn(img_bgr, *args, **kwargs)
        _write_cache("ocr_crop", h, res)
        return res

    return wrapper


def wrap_ocr_recognize_line(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(img_bgr, *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(img_bgr, *args, **kwargs)

        h = _hash_array(img_bgr)
        if not is_refresh_enabled():
            cached = _read_cache("ocr_line", h)
            if cached is not None:
                return cached

        res = orig_fn(img_bgr, *args, **kwargs)
        _write_cache("ocr_line", h, res)
        return res

    return wrapper


def wrap_inpaint_call(orig_fn: Callable) -> Callable:
    @functools.wraps(orig_fn)
    def wrapper(img_bgr, mask, mode="patch", *args, **kwargs):
        if is_cache_disabled():
            return orig_fn(img_bgr, mask, mode=mode, *args, **kwargs)

        h = f"{_hash_array(img_bgr)}_{_hash_array(mask)}_{mode}"
        if not is_refresh_enabled():
            cached = _read_cache("lama", h)
            if cached is not None:
                return cached

        res = orig_fn(img_bgr, mask, mode=mode, *args, **kwargs)
        _write_cache("lama", h, res)
        return res

    return wrapper


def patch_all_models() -> None:
    """Apply caching wrappers to ComicTextDetector, RapidOCR, and LamaInpainter."""
    from app import detect, inpaint, lama, ocr

    if not getattr(detect.ComicTextDetector.analyze, "_is_cached", False):
        orig_ctd = detect.ComicTextDetector.analyze
        wrapped_ctd = wrap_detector_analyze(orig_ctd)
        wrapped_ctd._is_cached = True
        wrapped_ctd._orig = orig_ctd
        detect.ComicTextDetector.analyze = wrapped_ctd

    if not getattr(ocr._run_engine, "_is_cached", False):
        orig_engine = ocr._run_engine
        wrapped_engine = wrap_ocr_run_engine(orig_engine)
        wrapped_engine._is_cached = True
        wrapped_engine._orig = orig_engine
        ocr._run_engine = wrapped_engine

    if not getattr(ocr.recognize_full, "_is_cached", False):
        orig_rf = ocr.recognize_full
        wrapped_rf = wrap_ocr_recognize_full(orig_rf)
        wrapped_rf._is_cached = True
        wrapped_rf._orig = orig_rf
        ocr.recognize_full = wrapped_rf

    if not getattr(ocr.recognize_crop, "_is_cached", False):
        orig_rc = ocr.recognize_crop
        wrapped_rc = wrap_ocr_recognize_crop(orig_rc)
        wrapped_rc._is_cached = True
        wrapped_rc._orig = orig_rc
        ocr.recognize_crop = wrapped_rc

    if not getattr(ocr.recognize_line, "_is_cached", False):
        orig_rl = ocr.recognize_line
        wrapped_rl = wrap_ocr_recognize_line(orig_rl)
        wrapped_rl._is_cached = True
        wrapped_rl._orig = orig_rl
        ocr.recognize_line = wrapped_rl

    if not getattr(inpaint._lama_inpaint, "_is_cached", False):
        orig_inpaint = inpaint._lama_inpaint
        wrapped_inpaint = wrap_inpaint_call(orig_inpaint)
        wrapped_inpaint._is_cached = True
        wrapped_inpaint._orig = orig_inpaint
        inpaint._lama_inpaint = wrapped_inpaint

