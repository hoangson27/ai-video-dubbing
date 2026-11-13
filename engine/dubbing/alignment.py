import math
import wave
from array import array
from pathlib import Path

RATE = 48_000
MAX_FIT_SPEED = 1.30
BLOCK = 16_384


def frame_count(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, RATE):
            raise ValueError(f"WAV phải là PCM mono 16-bit, {RATE} Hz: {path.name}")
        return wav.getnframes()


def write_silence(output, frames, checkpoint):
    silence = bytes(2 * BLOCK)
    while frames:
        checkpoint()
        length = min(BLOCK, frames)
        output.writeframesraw(silence[:2 * length])
        frames -= length


def write_normalized(output, path, frames, checkpoint):
    peak = 0
    power = 0
    with wave.open(str(path), "rb") as wav:
        for offset in range(0, frames, BLOCK):
            checkpoint()
            samples = array("h", wav.readframes(min(BLOCK, frames - offset)))
            peak = max(peak, max(map(abs, samples), default=0))
            power += sum(sample * sample for sample in samples)
        if not peak:
            write_silence(output, frames, checkpoint)
            return
        gain = min(4.0, 3_680 / math.sqrt(power / frames), 29_200 / peak)
        fade = min(frames // 2, round(0.04 * RATE))
        wav.rewind()
        for offset in range(0, frames, BLOCK):
            checkpoint()
            samples = array("h", wav.readframes(min(BLOCK, frames - offset)))
            for index, sample in enumerate(samples):
                position = offset + index
                envelope = min(1.0, position / fade, (frames - 1 - position) / fade) if fade else 1.0
                samples[index] = round(sample * gain * envelope)
            output.writeframesraw(samples.tobytes())


def align(segments: list[dict], duration: float, destination: Path, retime, checkpoint) -> dict[str, str]:
    """Lay out PCM audio in non-overlapping video-relative slots."""
    total = round(duration * RATE)
    if total <= 0 or 2 * total >= (2**32 - 44):
        raise ValueError("Thời lượng vượt giới hạn của WAV PCM 48 kHz.")
    ordered = sorted(segments, key=lambda row: (row["start"], row["end"]))
    warnings = {}
    temporary = destination.with_suffix(".part.wav")
    fitted = destination.with_suffix(".fit.wav")
    cursor = 0
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            for index, segment in enumerate(ordered):
                checkpoint()
                start = max(cursor, min(total, round(segment["start"] * RATE)))
                end = min(total, round(segment["end"] * RATE))
                next_start = round(ordered[index + 1]["start"] * RATE) if index + 1 < len(ordered) else total
                notes = []
                if next_start < end:
                    notes.append("Trùng thời gian với đoạn kế tiếp.")
                capacity = min(end, next_start) - start
                if capacity > 0:
                    source = Path(segment["audio_path"])
                    length = frame_count(source)
                    if length > capacity:
                        speed = min(MAX_FIT_SPEED, length / capacity)
                        retime(source, fitted, speed)
                        if fitted.is_file():
                            source = fitted
                            length = frame_count(fitted)
                        if length > capacity:
                            if length - capacity > RATE * 0.02:
                                notes.append("Lời đọc vượt khung và đã được cắt. Hãy rút ngắn bản dịch.")
                            length = capacity
                    write_silence(output, start - cursor, checkpoint)
                    write_normalized(output, source, length, checkpoint)
                    cursor = start + length
                    fitted.unlink(missing_ok=True)
                else:
                    notes.append("Đoạn nằm ngoài khoảng thoại khả dụng.")
                if notes:
                    warnings[segment["id"]] = " ".join(notes)
            write_silence(output, total - cursor, checkpoint)
        checkpoint()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
        fitted.unlink(missing_ok=True)
    return warnings
