"""Centralized config loader for McDonald's AI Drive-Thru backend.

Loads config.yaml once at startup and exposes it via get_config().
Fail-fast: raises on missing or malformed config file.
"""

import os
from pathlib import Path
from typing import Any

import yaml

__all__ = ["get_config", "reload_config", "get_local_mode_config"]

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_cache: dict[str, Any] | None = None

# Defaults for local_mode — used when the section is missing from config.yaml
_LOCAL_MODE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "model_path": "./models/phi4-multimodal",
    "tts_default_voice": "en_US-amy-medium",
    "tts_length_scale": 0.9,
    "tts_available_voices": [
        "en_US-amy-medium",
        "en_GB-jenny_dioco-medium",
        "en_US-lessac-medium",
        "en_US-kristin-medium",
    ],
    "tts_model_path": "./models/piper",
    "device": "auto",
    "max_length": 256,
    "temperature": 0.6,
    "tts_sample_rate": 24000,
    "lazy_load": True,
    "stt_model": "small",
    "stt_device": "auto",
    "stt_compute_type": "int8",
}


def _load() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {_CONFIG_PATH}")
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(data).__name__}")
    # Validate required top-level sections
    required = {"model", "business_rules", "cache", "audio", "connection"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Config file missing required sections: {missing}")
    return data


def get_config() -> dict[str, Any]:
    """Return the cached config dict. Loads from disk on first call."""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def reload_config() -> dict[str, Any]:
    """Force-reload config from disk. Used by DEV_MODE hot-reload."""
    global _cache
    _cache = _load()
    return _cache


def get_local_mode_config() -> dict[str, Any]:
    """Get local mode configuration with environment variable overrides.

    Priority: environment variables > config.yaml > built-in defaults.
    Environment variable overrides:
    - LOCAL_MODE_ENABLED  → local_mode.enabled
    - LOCAL_MODE_MODEL_PATH → local_mode.model_path
    - LOCAL_MODE_DEVICE → local_mode.device
    - LOCAL_MODE_MAX_LENGTH → local_mode.max_length
    - LOCAL_MODE_TEMPERATURE → local_mode.temperature
    - LOCAL_MODE_LAZY_LOAD → local_mode.lazy_load
    - LOCAL_MODE_TTS_DEFAULT_VOICE → local_mode.tts_default_voice
    - LOCAL_MODE_TTS_MODEL_PATH → local_mode.tts_model_path
    - LOCAL_MODE_TTS_LENGTH_SCALE → local_mode.tts_length_scale
    - LOCAL_MODE_TTS_SAMPLE_RATE → local_mode.tts_sample_rate
    - LOCAL_MODE_STT_MODEL → local_mode.stt_model
    - LOCAL_MODE_STT_DEVICE → local_mode.stt_device
    - LOCAL_MODE_STT_COMPUTE_TYPE → local_mode.stt_compute_type
    """
    config = get_config()
    # Merge defaults ← yaml ← env overrides
    result = {**_LOCAL_MODE_DEFAULTS, **config.get("local_mode", {})}

    # Environment variable overrides take highest precedence
    if (env_enabled := os.environ.get("LOCAL_MODE_ENABLED")) is not None:
        result["enabled"] = env_enabled.strip().lower() in {"1", "true", "yes", "on"}
    if (env_path := os.environ.get("LOCAL_MODE_MODEL_PATH")) is not None:
        result["model_path"] = env_path
    if (env_device := os.environ.get("LOCAL_MODE_DEVICE")) is not None:
        result["device"] = env_device
    if (env_max_length := os.environ.get("LOCAL_MODE_MAX_LENGTH")) is not None:
        result["max_length"] = int(env_max_length)
    if (env_temperature := os.environ.get("LOCAL_MODE_TEMPERATURE")) is not None:
        result["temperature"] = float(env_temperature)
    if (env_lazy_load := os.environ.get("LOCAL_MODE_LAZY_LOAD")) is not None:
        result["lazy_load"] = env_lazy_load.strip().lower() in {"1", "true", "yes", "on"}
    if (env_tts_voice := os.environ.get("LOCAL_MODE_TTS_DEFAULT_VOICE")) is not None:
        result["tts_default_voice"] = env_tts_voice
    if (env_tts_path := os.environ.get("LOCAL_MODE_TTS_MODEL_PATH")) is not None:
        result["tts_model_path"] = env_tts_path
    if (env_tts_length_scale := os.environ.get("LOCAL_MODE_TTS_LENGTH_SCALE")) is not None:
        result["tts_length_scale"] = float(env_tts_length_scale)
    if (env_tts_sample_rate := os.environ.get("LOCAL_MODE_TTS_SAMPLE_RATE")) is not None:
        result["tts_sample_rate"] = int(env_tts_sample_rate)
    if (env_stt_model := os.environ.get("LOCAL_MODE_STT_MODEL")) is not None:
        result["stt_model"] = env_stt_model
    if (env_stt_device := os.environ.get("LOCAL_MODE_STT_DEVICE")) is not None:
        result["stt_device"] = env_stt_device
    if (env_stt_compute_type := os.environ.get("LOCAL_MODE_STT_COMPUTE_TYPE")) is not None:
        result["stt_compute_type"] = env_stt_compute_type

    return result
