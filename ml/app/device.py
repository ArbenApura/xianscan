# HARDWARE ACCELERATION & DEVICE AUTO-DETECTION MODULE
#
# STRICTLY ENFORCES DEDICATED GPU (dGPU) USAGE ONLY FOR HARDWARE ACCELERATION.
# INTEGRATED GPUS (iGPUs / APUs LIKE AMD Radeon(TM) Graphics OR Intel UHD/Iris) ARE BANNED
# FROM ML INFERENCE TO PREVENT DESKTOP WINDOW MANAGER (DWM) STARVATION, SYSTEM FREEZES,
# SHARED BUS CONTENTION, AND DRIVER TDR TIMEOUTS.
#
# AUTOMATIC DETECTION HIERARCHY:
# 1. NVIDIA CUDA / TensorRT (Dedicated NVIDIA GPUs)
# 2. DirectML (Dedicated AMD Radeon RX / Intel Arc / NVIDIA GPUs on Windows)
# 3. CoreML (Apple Silicon Metal on macOS)
# 4. AMD ROCm (Dedicated AMD GPUs on Linux)
# 5. CPU Multi-threaded (Universal Safe, Crash-Free Fallback)
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("translator.device")

_RESOLVED_PROVIDERS: list[Any] | None = None
_DEVICE_LABEL: str | None = None


def enumerate_system_gpus() -> list[dict[str, Any]]:
	"""ENUMERATES GRAPHICS ADAPTERS ON WINDOWS VIA NATIVE DXGI TO CLASSIFY DEDICATED VS INTEGRATED GPUS."""
	gpus: list[dict[str, Any]] = []
	if sys.platform != "win32":
		return gpus

	try:
		class LUID(ctypes.Structure):
			_fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

		class DXGI_ADAPTER_DESC(ctypes.Structure):
			_fields_ = [
				("Description", wintypes.WCHAR * 128),
				("VendorId", wintypes.UINT),
				("DeviceId", wintypes.UINT),
				("SubSysId", wintypes.UINT),
				("Revision", wintypes.UINT),
				("DedicatedVideoMemory", ctypes.c_size_t),
				("DedicatedSystemMemory", ctypes.c_size_t),
				("SharedSystemMemory", ctypes.c_size_t),
				("AdapterLuid", LUID),
			]

		dxgi = ctypes.oledll.dxgi
		factory = ctypes.c_void_p()

		class GUID(ctypes.Structure):
			_fields_ = [
				("Data1", wintypes.DWORD),
				("Data2", wintypes.WORD),
				("Data3", wintypes.WORD),
				("Data4", wintypes.BYTE * 8),
			]

		iid_factory = GUID(
			0x7B7166EC, 0x21C7, 0x44AE, (wintypes.BYTE * 8)(0xB2, 0x1A, 0xC9, 0xAE, 0x32, 0x1A, 0xE3, 0x69)
		)
		hr = dxgi.CreateDXGIFactory(ctypes.byref(iid_factory), ctypes.byref(factory))
		if hr == 0:
			vtbl = ctypes.cast(
				ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.POINTER(ctypes.c_void_p)
			)
			enum_adapters_func = ctypes.WINFUNCTYPE(
				ctypes.HRESULT, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
			)(vtbl[7])

			i = 0
			while True:
				adapter = ctypes.c_void_p()
				if enum_adapters_func(factory, i, ctypes.byref(adapter)) != 0:
					break
				adapter_vtbl = ctypes.cast(
					ctypes.cast(adapter, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.POINTER(ctypes.c_void_p)
				)
				get_desc_func = ctypes.WINFUNCTYPE(
					ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(DXGI_ADAPTER_DESC)
				)(adapter_vtbl[8])
				desc = DXGI_ADAPTER_DESC()
				if get_desc_func(adapter, ctypes.byref(desc)) == 0:
					name = str(desc.Description).strip()
					# Skip Microsoft Basic Render Driver (software rasterizer)
					if desc.VendorId != 0x1414 and "Basic Render" not in name:
						vram_mb = desc.DedicatedVideoMemory / (1024 * 1024)
						name_lower = name.lower()

						# Discrete GPU series identifiers
						is_known_dgpu = any(
							tag in name_lower
							for tag in [
								"geforce",
								"rtx",
								"gtx",
								"radeon rx",
								"radeon pro",
								"arc a",
								"arc(tm) a",
								"quadro",
								"tesla",
								"titan",
							]
						)
						# Integrated APU / iGPU identifiers
						is_known_igpu = any(
							tag in name_lower
							for tag in [
								"intel(r) hd",
								"intel(r) uhd",
								"intel(r) iris",
								"radeon(tm) graphics",
								"radeon vega",
							]
						)

						is_dedicated = is_known_dgpu or (vram_mb >= 1024 and not is_known_igpu)
						gpus.append(
							{
								"device_id": i,
								"name": name,
								"vendor_id": desc.VendorId,
								"vram_mb": round(vram_mb, 1),
								"is_dedicated": is_dedicated,
								"is_integrated": not is_dedicated,
							}
						)
				release_func = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(adapter_vtbl[2])
				release_func(adapter)
				i += 1

			release_factory = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])
			release_factory(factory)
	except Exception as e:
		logger.debug("DXGI adapter enumeration skipped/failed: %s", e)

	return gpus


def get_dedicated_gpu() -> dict[str, Any] | None:
	"""RETURNS THE PRIMARY DEDICATED GPU IF AVAILABLE."""
	for gpu in enumerate_system_gpus():
		if gpu.get("is_dedicated"):
			return gpu
	return None


def probe_hardware() -> tuple[list[Any], str]:
	"""PROBES THE ENVIRONMENT AND RETURNS (providers_list, human_readable_device_label).
	GUARANTEES THAT INTEGRATED GPUS ARE NEVER SELECTED IN AUTO MODE.
	"""
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

	dedicated_gpu = get_dedicated_gpu()
	detected_gpus = enumerate_system_gpus()
	has_only_igpu = bool(detected_gpus) and not any(g.get("is_dedicated") for g in detected_gpus)

	if env_override in ("cpu", "none"):
		providers = ["CPUExecutionProvider"]
		label = "CPU Multi-threaded"
	elif env_override in ("cuda", "gpu") and ("CUDAExecutionProvider" in available or "TensorrtExecutionProvider" in available):
		providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
		label = "NVIDIA CUDA GPU"
	elif env_override in ("dml", "directml") and "DmlExecutionProvider" in available:
		if dedicated_gpu:
			providers = [("DmlExecutionProvider", {"device_id": dedicated_gpu["device_id"]}), "CPUExecutionProvider"]
			label = f"DirectML Dedicated GPU ({dedicated_gpu['name']})"
		elif has_only_igpu:
			logger.warning(
				"DirectML requested but only Integrated GPU (%s) was detected. "
				"Defaulting to CPU Multi-threaded to protect against driver TDR hangs and crashes.",
				detected_gpus[0]["name"],
			)
			providers = ["CPUExecutionProvider"]
			label = "CPU Multi-threaded (iGPU Banned)"
		else:
			providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
			label = "DirectML (DirectX 12)"
	elif env_override in ("coreml", "apple") and "CoreMLExecutionProvider" in available:
		providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
		label = "Apple Silicon Metal (CoreML)"
	else:
		# 2. AUTO-DETECTION HIERARCHY — STRICTLY REQUIRES DEDICATED HARDWARE
		if "TensorrtExecutionProvider" in available:
			providers = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
			label = "NVIDIA TensorRT GPU"
		elif "CUDAExecutionProvider" in available:
			providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
			label = "NVIDIA CUDA GPU"
		elif "DmlExecutionProvider" in available and dedicated_gpu:
			# ONLY SELECT DIRECTML IF A GENUINE DEDICATED GPU IS DETECTED
			providers = [("DmlExecutionProvider", {"device_id": dedicated_gpu["device_id"]}), "CPUExecutionProvider"]
			label = f"DirectML Dedicated GPU ({dedicated_gpu['name']})"
		elif "ROCMExecutionProvider" in available:
			providers = ["ROCMExecutionProvider", "CPUExecutionProvider"]
			label = "AMD ROCm GPU"
		elif "CoreMLExecutionProvider" in available:
			providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
			label = "Apple Silicon Metal (CoreML)"
		else:
			if has_only_igpu:
				logger.info(
					"Integrated GPU detected (%s). Skipping DirectML to prevent system freezing/TDR crashes; using optimized multi-threaded CPU.",
					detected_gpus[0]["name"],
				)
			providers = ["CPUExecutionProvider"]
			label = "CPU Multi-threaded"

	_RESOLVED_PROVIDERS = providers
	_DEVICE_LABEL = label
	return providers, label


def get_ort_providers() -> list[Any]:
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
	is_dml = any(
		p == "DmlExecutionProvider" or (isinstance(p, (tuple, list)) and p[0] == "DmlExecutionProvider")
		for p in providers
	)
	if "lama" in str(model_path).lower() and is_dml:
		providers = [
			p
			for p in providers
			if p != "DmlExecutionProvider" and not (isinstance(p, (tuple, list)) and p[0] == "DmlExecutionProvider")
		] or ["CPUExecutionProvider"]

	try:
		session = ort.InferenceSession(str(model_path), providers=providers, sess_options=opts)
		active = session.get_providers()
		logger.info(
			"Loaded ONNX model [%s] with active provider: %s",
			os.path.basename(str(model_path)),
			active[0] if active else "Unknown",
		)
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


def set_active_provider(mode: str) -> tuple[list[Any], str]:
	"""DYNAMICALLY SWITCHES THE ACTIVE HARDWARE PROVIDER AND RELOADS RUNNING SESSIONS."""
	global _RESOLVED_PROVIDERS, _DEVICE_LABEL
	clean_mode = mode.lower().strip()
	os.environ["MT_DEVICE"] = clean_mode
	_RESOLVED_PROVIDERS = None
	_DEVICE_LABEL = None

	providers, label = probe_hardware()

	# HOT-RELOAD RUNNING SESSIONS ACROSS ALL PIPELINE STAGES
	try:
		from . import pipeline, ocr, inpaint

		if pipeline.detector is not None:
			pipeline.detector._session = None
		ocr._engine = None
		inpaint._lama_model = None
		inpaint._lama_ready.clear()
	except Exception as e:
		logger.warning("Could not reset model sessions on device switch: %s", e)

	return providers, label


def get_hardware_status() -> dict[str, Any]:
	"""RETURNS STRUCTURED HARDWARE & PROVIDER DIAGNOSTICS."""
	providers, label = probe_hardware()
	import onnxruntime as ort

	available = ort.get_available_providers()
	detected_gpus = enumerate_system_gpus()
	dedicated_gpu = get_dedicated_gpu()
	has_dedicated_gpu = bool(dedicated_gpu) or ("CUDAExecutionProvider" in available or "TensorrtExecutionProvider" in available)

	gpu_warning = None
	if detected_gpus and not has_dedicated_gpu:
		gpu_warning = (
			f"Integrated GPU detected ({detected_gpus[0]['name']}). GPU acceleration is disabled to prevent "
			"desktop freezing and driver crashes. Running on multi-threaded CPU."
		)

	active_name = providers[0] if providers else "CPUExecutionProvider"
	if isinstance(active_name, (tuple, list)):
		active_name = active_name[0]

	return {
		"device_label": label,
		"active_provider": active_name,
		"providers": [p[0] if isinstance(p, (tuple, list)) else p for p in providers],
		"available_providers": available,
		"has_cuda": "CUDAExecutionProvider" in available or "TensorrtExecutionProvider" in available,
		"has_directml": "DmlExecutionProvider" in available and bool(dedicated_gpu),
		"has_directml_raw": "DmlExecutionProvider" in available,
		"has_coreml": "CoreMLExecutionProvider" in available,
		"has_dedicated_gpu": has_dedicated_gpu,
		"detected_gpus": detected_gpus,
		"gpu_warning": gpu_warning,
	}


def get_rapidocr_params() -> dict[str, Any]:
	"""BUILDS HARDWARE-SPECIFIC CONFIGURATION PARAMETERS FOR RAPIDOCR ENGINE."""
	providers = get_ort_providers()
	is_cuda = any(
		p == "CUDAExecutionProvider" or (isinstance(p, (tuple, list)) and p[0] == "CUDAExecutionProvider")
		for p in providers
	)
	is_dml = any(
		p == "DmlExecutionProvider" or (isinstance(p, (tuple, list)) and p[0] == "DmlExecutionProvider")
		for p in providers
	)
	is_coreml = any(
		p == "CoreMLExecutionProvider" or (isinstance(p, (tuple, list)) and p[0] == "CoreMLExecutionProvider")
		for p in providers
	)

	if is_cuda:
		return {"Det.engine_cfg.use_cuda": True, "Rec.engine_cfg.use_cuda": True, "Cls.engine_cfg.use_cuda": True}
	if is_dml:
		return {"Det.engine_cfg.use_dml": True, "Rec.engine_cfg.use_dml": True, "Cls.engine_cfg.use_dml": True}
	if is_coreml:
		return {"Det.engine_cfg.use_coreml": True, "Rec.engine_cfg.use_coreml": True, "Cls.engine_cfg.use_coreml": True}
	return {}


