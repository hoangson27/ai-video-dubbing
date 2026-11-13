"""Explicit model downloads for setup-deps.ps1. The desktop engine never imports this module."""

import argparse
import json
import uuid
from pathlib import Path

from .providers import (
    ONNX_CODEC_REPO, WhisperIntegrityError, tts_cpu_weights, tts_gpu_weights, whisper_model_path, whisper_weights,
)
from .jobs import safe_tree

VIENEU_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
VIENEU_REVISION = "61b85e3d937fbbacb387714180e8182823512523"
CODEC_ONNX_REVISION = "ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae"
CODEC_GPU_REVISION = "6aa02b01e445cc585582cf0ba480bc3ea6c8dd68"


def repair_whisper(settings: dict, data_dir: Path, download_model) -> None:
    whisper = whisper_model_path(settings, data_dir)
    safe_tree(whisper)
    whisper.parent.mkdir(parents=True, exist_ok=True)
    staging = whisper.parent / f".{whisper.name}-download-{uuid.uuid4().hex}"
    staged = whisper_model_path(settings, staging)
    for path in (staging, staged):
        if not path.resolve().is_relative_to(data_dir.resolve()):
            raise ValueError("Whisper download staging must stay inside the app data directory.")
        safe_tree(path)
    # Keep failed download directories for diagnosis/resume; preserve the active model until validation succeeds.
    download_model(whisper.name, output_dir=str(staged))
    safe_tree(staged)
    whisper_weights(settings, staging)
    backup = whisper.with_name(f"{whisper.name}.backup-{uuid.uuid4().hex}") if whisper.exists() else None
    safe_tree(whisper)
    if backup:
        safe_tree(backup)
        whisper.rename(backup)
    try:
        staged.rename(whisper)
    except OSError:
        if backup and not whisper.exists():
            backup.rename(whisper)
        raise
    if backup:
        print(f"Preserved previous Whisper weights: {backup}", flush=True)
    for empty in (staged.parent, staged.parent.parent, staging):
        try:
            empty.rmdir()
        except OSError:
            break


def main():
    from faster_whisper.utils import download_model
    from huggingface_hub import snapshot_download

    parser = argparse.ArgumentParser(description="Download explicitly requested local model weights.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--tts-model-path", type=Path)
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()
    models = args.data_dir / "models"
    tts = args.tts_model_path or models / "vieneu"
    if not args.data_dir.is_absolute() or not tts.is_absolute():
        parser.error("model directories must be absolute local directories")
    whisper = whisper_model_path({"whisper_model": args.whisper_model}, args.data_dir)
    try:
        whisper_weights({"whisper_model": args.whisper_model}, args.data_dir)
    except (FileNotFoundError, WhisperIntegrityError):
        print(f"Downloading Whisper {args.whisper_model}...", flush=True)
        repair_whisper({"whisper_model": args.whisper_model}, args.data_dir, download_model)
    else:
        print(f"Whisper weights already available: {whisper}", flush=True)

    try:
        tts_cpu_weights(tts)
        if args.gpu:
            tts_gpu_weights(tts, args.data_dir)
    except FileNotFoundError:
        pass
    else:
        print(f"VieNeu-TTS weights already available: {tts}", flush=True)
        return
    marker = tts / ".ai-video-dubbing-download.json"
    receipt = {
        "repo": VIENEU_REPO, "revision": VIENEU_REVISION,
        "codec_cpu": CODEC_ONNX_REVISION, "codec_gpu": CODEC_GPU_REVISION,
    }
    custom = tts.resolve() != (models / "vieneu").resolve()
    if custom and tts.exists() and any(tts.iterdir()):
        try:
            managed = json.loads(marker.read_text(encoding="utf-8")) == receipt
        except (OSError, ValueError):
            managed = False
        if not managed:
            raise ValueError(
                "The selected VieNeu-TTS directory is incomplete. Choose an empty directory "
                "or the default model directory before downloading; existing custom weights are preserved."
            )
    if custom:
        tts.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(receipt), encoding="utf-8")
    print(f"Downloading VieNeu-TTS weights into {tts}...", flush=True)
    assets = ["onnx_update/*", "denoiser.onnx", "README.md"]
    if args.gpu:
        assets.extend(["update/*", "speaker_encoder.onnx"])
    snapshot_download(VIENEU_REPO, revision=VIENEU_REVISION, local_dir=tts, allow_patterns=assets)

    codec = Path(snapshot_download(
        ONNX_CODEC_REPO, revision=CODEC_ONNX_REVISION, cache_dir=tts / "hub",
        allow_patterns=["*.onnx", "*.data", "*.json", "README.md"], max_workers=1,
    ))
    # The SDK requests the default ref. Point it at the pinned snapshot for offline use.
    reference = codec.parent.parent / "refs" / "main"
    reference.parent.mkdir(parents=True, exist_ok=True)
    temporary = reference.with_suffix(".part")
    temporary.write_text(CODEC_ONNX_REVISION, encoding="utf-8")
    temporary.replace(reference)

    if args.gpu:
        snapshot_download(
            "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano", revision=CODEC_GPU_REVISION,
            local_dir=models / "vieneu-codec-gpu",
            allow_patterns=["*.py", "*.json", "*.safetensors", "README.md"],
        )


if __name__ == "__main__":
    main()
