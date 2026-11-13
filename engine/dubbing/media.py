import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from .alignment import RATE

DEMUXERS = {".mp4": "mov", ".mov": "mov", ".mkv": "matroska"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resolve_tool(settings: dict, name: str) -> str:
    configured = settings.get(f"{name}_path", "")
    if configured:
        path = Path(configured)
        if not path.is_absolute() or (os.name == "nt" and path.suffix.lower() != ".exe"):
            raise ValueError(f"Chọn đường dẫn tuyệt đối tới {name}.exe.")
        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy {name}: {path}")
        return str(path.resolve())
    if name == "ffprobe" and settings.get("ffmpeg_path"):
        suffix = ".exe" if os.name == "nt" else ""
        adjacent = Path(settings["ffmpeg_path"]).with_name(f"ffprobe{suffix}")
        if adjacent.is_absolute() and adjacent.is_file():
            return str(adjacent.resolve())
    executable = shutil.which(name)
    if not executable:
        raise FileNotFoundError(f"Không tìm thấy {name}. Kiểm tra PATH hoặc Cài đặt.")
    return str(Path(executable).resolve())


def run_tool(args, timeout, stdout=subprocess.PIPE):
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE,
            timeout=timeout, creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{Path(args[0]).stem} vượt thời gian chờ.") from error
    if result.returncode:
        details = result.stderr.decode("utf-8", errors="replace").strip()[-3500:]
        raise RuntimeError(f"{Path(args[0]).stem} (mã {result.returncode}): {details or 'Không có thông báo chi tiết.'}")
    return result.stdout


def number(value, default=0.0):
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else default
    except (TypeError, ValueError):
        return default


def rate(value):
    try:
        return float(Fraction(value))
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0


def validate_video(path):
    if path.suffix.lower() not in DEMUXERS:
        raise ValueError("Chỉ hỗ trợ tệp MP4, MKV và MOV.")
    if not path.is_absolute() or not path.is_file() or not path.stat().st_size:
        raise ValueError("Video không tồn tại hoặc tệp rỗng.")


def input_format(path):
    return ["-protocol_whitelist", "file,pipe", "-f", DEMUXERS[path.suffix.lower()]]


class Media:
    def __init__(self, settings):
        self.settings = settings

    def ffmpeg(self, args, timeout=7_200):
        return run_tool([
            resolve_tool(self.settings, "ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", *args,
        ], timeout)

    def ffprobe(self, args, timeout=35, stdout=subprocess.PIPE):
        return run_tool([resolve_tool(self.settings, "ffprobe"), "-v", "error", *args], timeout, stdout)

    def video_span(self, path, video_index, fps):
        """Read video packet timing when the container omits the stream duration."""
        count = 0
        first = math.inf
        end = -math.inf
        fallback_duration = 1 / fps if fps else 0
        with tempfile.TemporaryFile() as packets:
            self.ffprobe([
                *input_format(path), "-select_streams", str(video_index), "-show_packets",
                "-show_entries", "packet=pts_time,dts_time,duration_time", "-of", "compact=p=0", str(path),
            ], timeout=75, stdout=packets)
            packets.seek(0)
            for line in packets:
                fields = dict(field.split(b"=", 1) for field in line.strip().split(b"|") if b"=" in field)
                timestamp = number(fields.get(b"pts_time"), number(fields.get(b"dts_time"), math.inf))
                if math.isfinite(timestamp):
                    length = number(fields.get(b"duration_time")) or fallback_duration
                    first = min(first, timestamp)
                    end = max(end, timestamp + length)
                    count += 1
        return (first, end - first, count) if count and end > first else None

    def probe(self, path: Path) -> dict:
        validate_video(path)
        data = json.loads(self.ffprobe([
            *input_format(path), "-show_format", "-show_streams", "-of", "json", str(path),
        ]))
        streams = data.get("streams", [])
        video = next((s for s in streams if s["codec_type"] == "video"
                      and not s.get("disposition", {}).get("attached_pic")), None)
        if not video:
            raise ValueError("Tệp không có luồng hình ảnh hợp lệ.")
        container = data.get("format", {})
        video_start = number(video.get("start_time"), number(container.get("start_time")))
        duration = number(video.get("duration"))
        frame_total = int(number(video.get("nb_frames")))
        fps = rate(video.get("avg_frame_rate")) or rate(video.get("r_frame_rate"))
        if not duration:
            span = self.video_span(path, video["index"], fps)
            if span:
                video_start, duration, frame_total = span
        width = video.get("width", 0)
        height = video.get("height", 0)
        if duration <= 0 or not width or not height or not video.get("codec_name"):
            raise ValueError("Không đọc được codec, độ phân giải hoặc thời lượng video.")
        audio = sorted((s for s in streams if s["codec_type"] == "audio"),
                       key=lambda s: not s.get("disposition", {}).get("default"))
        return {
            "duration": duration, "width": width, "height": height,
            "fps": frame_total / duration if frame_total else fps,
            "codec": video["codec_name"], "size": path.stat().st_size,
            "video_index": video["index"], "video_start": video_start, "video_frames": frame_total,
            "audio_tracks": [
                {"index": stream["index"], "codec": stream.get("codec_name"),
                 "channels": stream.get("channels"), "language": stream.get("tags", {}).get("language")}
                for stream in audio
            ],
        }

    def extract(self, source: Path, metadata: dict, destination: Path):
        if not metadata["audio_tracks"]:
            raise ValueError("Video không có luồng âm thanh.")
        stream = metadata["audio_tracks"][0]["index"]
        start = metadata["video_start"]
        self.ffmpeg([
            "-y", "-copyts", *input_format(source), "-i", str(source), "-map", f"0:{stream}", "-vn",
            "-af", f"asetpts=PTS-({start:.9f})/TB,aresample=16000:async=1:first_pts=0,apad",
            "-t", str(metadata["duration"]), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
        ])

    def retime(self, source: Path, destination: Path, speed: float):
        filters = ["-af", f"atempo={speed:.8f}"] if speed != 1 else []
        self.ffmpeg([
            "-y", "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn",
            *filters, "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
            str(destination),
        ], timeout=300)

    def preview(self, source: Path, destination: Path):
        metadata = self.probe(source)
        args = ["-map", f"0:{metadata['video_index']}"]
        if metadata["audio_tracks"]:
            args.extend(["-map", f"0:{metadata['audio_tracks'][0]['index']}"])
        self.ffmpeg([
            "-y", *input_format(source), "-i", str(source), *args, "-t", "90",
            "-vf", "scale=w='max(2,trunc(min(960,iw)/2)*2)':h=-2", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "27", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(destination),
        ], timeout=240)

    def mux(self, source: Path, original: dict, audio: Path, destination: Path):
        if destination.resolve() == source.resolve() or destination.exists():
            raise ValueError("Đường dẫn xuất trùng tệp đã tồn tại.")
        args = ["-movflags", "+faststart"] if destination.suffix == ".mp4" else []
        self.ffmpeg([
            "-n", "-copyts", "-itsoffset", f"{-original['video_start']:.9f}",
            *input_format(source), "-i", str(source),
            "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", str(audio),
            "-map", f"0:{original['video_index']}", "-map", "1:a:0", "-map_metadata", "0",
            "-map_chapters", "-1", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-metadata:s:a:0", "language=vie", "-disposition:a:0", "default", *args, str(destination),
        ])
        exported = self.probe(destination)
        if any(exported[key] != original[key] for key in ("width", "height", "codec")):
            raise RuntimeError("Luồng hình ảnh xuất ra khác codec hoặc kích thước của nguồn.")
        if original["video_frames"] and exported["video_frames"] and original["video_frames"] != exported["video_frames"]:
            raise RuntimeError("Số khung hình xuất ra khác với nguồn.")
        if original["fps"] and abs(exported["fps"] - original["fps"]) > max(0.03, 0.005 * original["fps"]):
            raise RuntimeError("Tốc độ khung hình được FFprobe báo khác với nguồn.")
        if not exported["audio_tracks"] or exported["duration"] + 0.10 < original["duration"]:
            raise RuntimeError("Video xuất bị thiếu âm thanh hoặc ngắn hơn nguồn.")

    def packet_digest(self, path: Path) -> str:
        metadata = self.probe(path)
        digest = hashlib.sha256()
        with tempfile.TemporaryFile() as packets:
            self.ffprobe([
                *input_format(path), "-select_streams", str(metadata["video_index"]), "-show_packets",
                "-show_entries", "packet=data_hash", "-show_data_hash", "sha256", "-of", "csv=p=0", str(path),
            ], timeout=900, stdout=packets)
            packets.seek(0)
            for packet in packets:
                fields = packet.strip().split(b",")
                digest.update(b"".join(field for field in fields if field.startswith(b"SHA256:")))
        if digest.digest() == hashlib.sha256().digest():
            raise RuntimeError("Không đọc được packet hash của luồng hình ảnh.")
        return digest.hexdigest()
