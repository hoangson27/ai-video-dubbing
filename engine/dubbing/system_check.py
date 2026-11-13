"""Inspect local runtimes and model files without starting inference or downloading."""

import csv
import ctypes
import importlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from . import providers
from .media import resolve_tool
from .providers import whisper_cuda
from .profile import is_dev_mode, ollama_endpoint, sanitize_dev_settings


def _command(arguments: list[str], timeout: int = 6) -> str:
    result = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
        check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.stdout.decode("utf-8-sig", errors="replace").strip()


def _windows_hardware() -> tuple[str | None, float | None]:
    import winreg

    cpu = os.environ.get("PROCESSOR_IDENTIFIER")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        pass

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total_physical", ctypes.c_uint64),
            ("available_physical", ctypes.c_uint64), ("total_page_file", ctypes.c_uint64),
            ("available_page_file", ctypes.c_uint64), ("total_virtual", ctypes.c_uint64),
            ("available_virtual", ctypes.c_uint64), ("available_extended_virtual", ctypes.c_uint64),
        ]

    memory = MemoryStatus(length=ctypes.sizeof(MemoryStatus))
    ram_gb = None
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        ram_gb = round(memory.total_physical / (1024 ** 3), 1)
    return cpu, ram_gb


def _inventory(cuda_devices: int) -> dict:
    cpu = platform.processor() or platform.machine() or "Chưa xác định"
    ram_gb = None
    gpus = []
    if os.name == "nt":
        cpu_name, ram_gb = _windows_hardware()
        cpu = cpu_name or cpu
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        query = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$ErrorActionPreference = 'Stop'; "
            "@{cpu=(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name); "
            "ram=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory; "
            "gpu=@(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name)} "
            "| ConvertTo-Json -Compress"
        )
        try:
            inventory = json.loads(_command([str(powershell), "-NoProfile", "-NonInteractive", "-Command", query]))
            cpu = inventory.get("cpu") or cpu
            ram = inventory.get("ram")
            ram_gb = round(ram / (1024 ** 3), 1) if ram else ram_gb
            gpus = [{"name": name, "vram_mb": None} for name in dict.fromkeys(inventory.get("gpu") or [])]
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    elif hasattr(os, "sysconf"):
        try:
            ram_gb = round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3), 1)
        except (OSError, ValueError):
            pass

    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            output = _command([nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
            nvidia = []
            for row in csv.reader(output.splitlines()):
                if len(row) != 2:
                    continue
                name, memory = (field.strip() for field in row)
                nvidia.append({"name": name, "vram_mb": int(memory) if memory.isdigit() else None})
            if nvidia:
                gpus = nvidia + [item for item in gpus if "nvidia" not in item["name"].lower()]
        except (OSError, subprocess.SubprocessError):
            pass
    return {"cpu": cpu, "ram_gb": ram_gb, "gpus": gpus, "cuda_devices": cuda_devices}


def _package(distribution: str, module: str, version: str | None = None) -> tuple[str, str]:
    try:
        installed = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "missing", f"Chưa cài {distribution}. Hãy chạy setup-deps.ps1."
    if version and installed != version:
        return "incompatible", f"Cần {distribution} {version}, hiện có {installed}. Hãy chạy setup-deps.ps1."
    try:
        importlib.import_module(module)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return "incompatible", f"Không nạp được {module}: {exc}"
    return "installed", f"{distribution} {installed}"


def check(settings: dict, data_dir: Path) -> dict:
    settings = sanitize_dev_settings(settings, data_dir)
    checks = []

    def add(name: str, status: str, detail: str):
        checks.append({"name": name, "status": status, "detail": detail})

    modes = {}
    for stage, label in (("asr", "ASR"), ("translation", "dịch"), ("tts", "TTS")):
        try:
            modes[stage] = providers.device_mode(settings, stage)
        except ValueError as exc:
            modes[stage] = "auto"
            add(f"Thiết bị {label}", "incompatible", str(exc))
    try:
        providers.configure_offline(settings, data_dir)
    except ValueError:
        providers.configure_offline({}, data_dir)
    python = platform.python_version()
    compatible_python = sys.version_info[:2] == (3, 11) and struct.calcsize("P") == 8
    add("Python", "installed" if compatible_python else "incompatible",
        f"{python} · {sys.executable}" if compatible_python else f"Cần Python 3.11 x64, hiện dùng {python} · {sys.executable}")
    venv = sys.prefix != sys.base_prefix
    add("Virtual environment", "installed" if venv else "missing",
        sys.prefix if venv else "Chạy setup-deps.ps1 để tạo môi trường ảo riêng.")

    for tool in ("ffmpeg", "ffprobe"):
        try:
            executable = resolve_tool(settings, tool)
            version = _command([executable, "-version"]).splitlines()
            if not version or not version[0].lower().startswith(f"{tool} version"):
                raise ValueError(f"{executable} không trả về phiên bản {tool} hợp lệ.")
            add(tool.upper(), "installed", f"{version[0]} · {executable}")
        except FileNotFoundError as exc:
            add(tool.upper(), "missing", str(exc))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            add(tool.upper(), "incompatible", str(exc))

    for distribution, module, pinned in (
        ("faster-whisper", "faster_whisper", providers.WHISPER_VERSION),
        ("ctranslate2", "ctranslate2", providers.CT2_VERSION),
        ("av", "av", providers.AV_VERSION),
        ("vieneu", "vieneu.v3turbo", providers.VIENEU_VERSION),
        ("onnxruntime", "onnxruntime", None),
        ("numpy", "numpy", None),
        ("soundfile", "soundfile", None),
    ):
        add(distribution, *_package(distribution, module, pinned))

    try:
        directory = providers.whisper_weights(settings, data_dir)
        add("Whisper model", "installed", str(directory))
    except FileNotFoundError as exc:
        add("Whisper model", "missing", str(exc))
    except (OSError, ValueError) as exc:
        add("Whisper model", "incompatible", str(exc))
    count, reason = whisper_cuda()
    if modes["asr"] == "gpu" and (reason or not count):
        add("Whisper CUDA", "incompatible", reason or "Whisper không tìm thấy GPU CUDA.")
    elif modes["asr"] == "cpu":
        add("Whisper CUDA", "installed", "Whisper được đặt chạy CPU.")
    else:
        add("Whisper CUDA", "installed", f"AUTO dùng CPU. {reason}" if reason else f"{count} GPU CUDA khả dụng.")

    voices = []
    try:
        tts = providers.tts_model_path(settings, data_dir)
        if modes["tts"] == "gpu":
            providers.tts_gpu_weights(tts, data_dir)
            backend = "PyTorch/CUDA"
        elif modes["tts"] == "auto":
            try:
                providers.tts_gpu_weights(tts, data_dir)
                backend = "PyTorch/CUDA" if not providers.tts_cuda() else "ONNX/CPU"
            except FileNotFoundError:
                backend = "ONNX/CPU"
        else:
            backend = "ONNX/CPU"
        if backend == "ONNX/CPU":
            providers.tts_cpu_weights(tts)
        add("VieNeu-TTS model", "installed", f"{backend} · {tts}")
    except (FileNotFoundError, ImportError) as exc:
        add("VieNeu-TTS model", "missing", str(exc))
    except (OSError, RuntimeError, ValueError) as exc:
        add("VieNeu-TTS model", "incompatible", str(exc))

    if modes["tts"] == "gpu":
        try:
            problem = providers.tts_cuda()
        except RuntimeError as exc:
            problem = str(exc)
        add("VieNeu-TTS CUDA", "incompatible" if problem else "installed", problem or "PyTorch CUDA khả dụng.")

    try:
        voices = providers.list_tts_voices(providers.tts_model_path(settings, data_dir))
        if not voices:
            raise ValueError("Model không có preset voice hợp lệ.")
        add("Giọng VieNeu-TTS", "installed", f"{len(voices)} giọng preset.")
    except metadata.PackageNotFoundError:
        add("Giọng VieNeu-TTS", "missing", "Cài vieneu để có bộ giọng preset.")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        add("Giọng VieNeu-TTS", "incompatible", str(exc))

    try:
        if is_dev_mode(settings) and settings.get("ollama_owned") is not True:
            raise providers.OllamaUnavailable("Ollama local 127.0.0.1:11435 chưa được ứng dụng xác minh quyền sở hữu. Hãy chạy thiết lập dev.")
        host, port = ollama_endpoint(settings)
        models = providers.ollama_models(settings)
    except providers.OllamaUnavailable as exc:
        add("Ollama", "missing", str(exc))
        add("Qwen", "missing", "Kiểm tra lại sau khi mở Ollama.")
    except (RuntimeError, ValueError) as exc:
        add("Ollama", "incompatible", str(exc))
        add("Qwen", "missing", "Không đọc được danh sách model local.")
    else:
        add("Ollama", "installed", f"Đã kết nối http://{host}:{port}.")
        try:
            name = providers.local_ollama_model(settings, models)
            detail = {
                "gpu": "Chế độ GPU xác minh VRAM khi dịch.",
                "cpu": "Dịch được đặt chạy CPU.",
                "auto": "Đã tìm thấy model local; AUTO do Ollama chọn thiết bị khi dịch.",
            }[modes["translation"]]
            add("Qwen", "installed", f"{name} · {detail}")
        except FileNotFoundError as exc:
            add("Qwen", "missing", str(exc))
        except (providers.OllamaModelIncompatible, ValueError) as exc:
            add("Qwen", "incompatible", str(exc))

    return {
        "ready": all(item["status"] == "installed" for item in checks),
        "checks": checks, "hardware": _inventory(count), "voices": voices,
    }
