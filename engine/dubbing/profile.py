"""The native dev profile remains authoritative over saved project settings."""

import os
from pathlib import Path


def is_dev_mode(settings: dict) -> bool:
    return os.environ.get("AIVD_DEV_MODE") == "1" or settings.get("dev_mode") is True


def sanitize_dev_settings(settings: dict, data_dir: Path) -> dict:
    settings = dict(settings)
    if not is_dev_mode(settings):
        return settings
    root = Path(data_dir)
    if not root.is_absolute():
        raise ValueError("Dev data directory must be absolute.")
    root = root.resolve()
    configured = os.environ.get("AIVD_DEV_ROOT")
    if configured and (not Path(configured).is_absolute() or Path(configured).resolve() != root):
        raise ValueError("AIVD_DEV_ROOT does not match the native dev data directory.")
    paths = {
        "ffmpeg_path": root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        "ffprobe_path": root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
        "python_path": root / "venv" / "Scripts" / "python.exe",
        "tts_model_path": root / "models" / "vieneu",
        "projects_dir": root / "projects",
        "output_dir": root / "output",
        "ollama_path": root / "tools" / "ollama" / "ollama.exe",
    }
    for name, path in paths.items():
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Dev path escapes the project data directory: {path}")
        settings[name] = str(path)
    settings.update(dev_mode=True, dev_root=str(root), ollama_host="127.0.0.1:11435")
    return settings


def ollama_endpoint(settings: dict | None = None) -> tuple[str, int]:
    if not is_dev_mode(settings or {}):
        return "127.0.0.1", 11434
    # No URL parsing, DNS, alternate IP notation, proxy, credentials or path is needed.
    allowed = {"127.0.0.1:11435", "http://127.0.0.1:11435"}
    for host in (os.environ.get("OLLAMA_HOST"), (settings or {}).get("ollama_host")):
        if host is not None and (not isinstance(host, str) or host not in allowed):
            raise ValueError("Dev Ollama requires the local endpoint 127.0.0.1:11435.")
    return "127.0.0.1", 11435
