# HARDWARE ACCELERATION & DEVICE AUTO-DETECTION MODULE
#
# AUTOMATICALLY DETECTS AND SELECTS THE BEST ONNX RUNTIME EXECUTION PROVIDER:
# 1. TensorRT / CUDA (NVIDIA Dedicated GPUs)
# 2. DirectML (AMD Radeon, Intel Arc/Iris/Xe, NVIDIA GPUs via DirectX 12 on Windows)
# 3. CoreML (Apple Silicon M-Series Macs)
# 4. ROCm (AMD GPUs on Linux)
# 5. CPU (Universal Safe Fallback)
#
# INCLUDES SELF-HEALING FALLBACK: IF A GPU PROVIDER FAILS AT RUNTIME, IT DROPS TO CPU
# SEAMLESSLY WITHOUT CRASHING THE SERVER.
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("translator.device")

_RESOLVED_PROVIDERS: list[str] | None = None
_DEVICE_LABEL: str | None = None


def probe_hardware() -> tuple[list[str], str]:
	"""PROBES THE ENVIRONMENT AND RETURNS (providers_list, human_readable_device_label)."""
	global _RESOLVED_PROVIDERS, _DEVICE_LABEL
	if _RESOLVED_PROVIDERS is not None and _DEVICE_LABEL is not None:
		return _RESOLVED_PROVIDERS, _DEVICE_LABEL

	# 1. CHECK FOR USER OVERRIDE IN MT_DEVICE
	env_override = os.environ.get("MT_DEVICE", "").strip().lower()

	try:
		import onnxruntime as ort

		available = ort.get_available_providers()
	except Exception as e:
		logger.warning("Could not query onnxruntime providers (%s). Defaulting to CPU.", e)
		available = ["CPUExecutionProvider"]

	if env_override in ("cpu", "none"):
		providers = ["CPUExecutionProvider"]
		label = "CPU (Forced via MT_DEVICE=cpu)"
	elif env_override in ("cuda", "gpu") and "CUDAExecutionProvider" in available:
		providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
		label = "NVIDIA CUDA (Forced via MT_DEVICE=cuda)"
	elif env_override in ("dml", "directml") and "DmlExecutionProvider" in available:
		providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
		label = "DirectML / DirectX 12 (Forced via MT_DEVICE=dml)"
	elif env_override in ("coreml", "apple") and "CoreMLExecutionProvider" in available:
		providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
		label = "Apple Silicon CoreML (Forced via MT_DEVICE=coreml)"
	else:
		# 2. AUTO-DETECTION HIERARCHY
		if "TensorrtExecutionProvider" in available:
			providers = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
			label = "NVIDIA TensorRT GPU"
		elif "CUDAExecutionProvider" in available:
			providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
			label = "NVIDIA CUDA GPU"
		elif "DmlExecutionProvider" in available:
			providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
			label = "DirectML (DirectX 12 / AMD & Intel & NVIDIA)"
		elif "ROCMExecutionProvider" in available:
			providers = ["ROCMExecutionProvider", "CPUExecutionProvider"]
			label = "AMD ROCm GPU"
		elif "CoreMLExecutionProvider" in available:
			providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
			label = "Apple Silicon CoreML / Metal"
		else:
			providers = ["CPUExecutionProvider"]
			label = "CPU (Standard)"

	_RESOLVED_PROVIDERS = providers
	_DEVICE_LABEL = label
	return providers, label


def get_ort_providers() -> list[str]:
	"""RETURNS THE LIST OF RESOLVED ONNX RUNTIME PROVIDERS."""
	providers, _ = probe_hardware()
	return list(providers)


def get_device_label() -> str:
	"""RETURNS HUMAN-READABLE HARDWARE DESCRIPTION (FOR LOGS & HEALTH ENDPOINT)."""
	_, label = probe_hardware()
	return label


def create_inference_session(model_path: str | os.PathLike, session_options: Any = None) -> Any:
	"""CREATES AN ONNX RUNTIME INFERENCE SESSION WITH AUTO-FALLBACK TO CPU ON DRIVER ERROR."""
	import onnxruntime as ort

	providers = get_ort_providers()
	opts = session_options or ort.SessionOptions()

	# LaMa's Fast Fourier Convolution (FFC) architecture uses dynamic complex tensor MatMuls
	# which are not supported by DirectML's HLSL compiler. Run LaMa on multi-threaded CPU when on DirectML.
	if "lama" in str(model_path).lower() and "DmlExecutionProvider" in providers:
		providers = [p for p in providers if p != "DmlExecutionProvider"] or ["CPUExecutionProvider"]

	try:
		session = ort.InferenceSession(str(model_path), providers=providers, sess_options=opts)
		active = session.get_providers()
		logger.info("Loaded ONNX model [%s] with active provider: %s", os.path.basename(str(model_path)), active[0] if active else "Unknown")
		return session
	except Exception as e:
		if providers != ["CPUExecutionProvider"]:
			logger.warning(
				"GPU session creation failed with providers %s (%s). Falling back gracefully to pure CPU.",
				providers,
				e,
			)
			session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"], sess_options=opts)
			return session
		raise


def get_rapidocr_params() -> dict[str, Any]:
	"""BUILDS HARDWARE-SPECIFIC CONFIGURATION PARAMETERS FOR RAPIDOCR ENGINE."""
	providers = get_ort_providers()
	if "CUDAExecutionProvider" in providers:
		return {"Det.engine_cfg.use_cuda": True, "Rec.engine_cfg.use_cuda": True, "Cls.engine_cfg.use_cuda": True}
	if "DmlExecutionProvider" in providers:
		return {"Det.engine_cfg.use_dml": True, "Rec.engine_cfg.use_dml": True, "Cls.engine_cfg.use_dml": True}
	if "CoreMLExecutionProvider" in providers:
		return {"Det.engine_cfg.use_coreml": True, "Rec.engine_cfg.use_coreml": True, "Cls.engine_cfg.use_coreml": True}
	return {}
