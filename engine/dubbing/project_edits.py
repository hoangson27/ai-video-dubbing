"""Validated project edits/imports and dependency invalidation."""
import math
import re
import shutil
import time
import uuid
import wave
from pathlib import Path

from . import providers
from .alignment import RATE, frame_count
from .jobs import digest, fingerprint, read_json
from .stages import invalidate, write_segments


class ProjectEdits:
    def reconcile_translation(self, job):
        """A partial edit cannot bless old automatic results under a new model/context."""
        if job.get("translation_key") and job["translation_key"] != self.stage_key(job, "translate"):
            invalidate(job, "translate", translations=True)

    @staticmethod
    def validate_wav(path, duration=None, *, source_audio=False):
        with wave.open(str(path), "rb") as wav:
            if wav.getcomptype() != "NONE" or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError("Audio phải là WAV PCM mono 16-bit.")
            rates = (16_000, RATE) if source_audio else (RATE,)
            if wav.getframerate() not in rates or wav.getnframes() <= 0:
                raise ValueError("Tần số hoặc thời lượng WAV không hợp lệ.")
            seconds = wav.getnframes() / wav.getframerate()
            if duration is not None and abs(seconds - duration) > max(.5, duration * .02):
                raise ValueError("Thời lượng audio thay thế không khớp video.")
            remaining = wav.getnframes()
            while remaining:
                requested = min(remaining, 65_536)
                if len(wav.readframes(requested)) != requested * 2:
                    raise ValueError("Tệp WAV bị thiếu dữ liệu.")
                remaining -= requested
        return seconds

    @staticmethod
    def validate_segments(segments, duration):
        if not isinstance(segments, list) or not segments or len(segments) > 100_000:
            raise ValueError("Transcript phải có ít nhất một đoạn hợp lệ.")
        seen = set()
        result = []
        for item in segments:
            identity = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identity, str) or not identity or len(identity) > 120 or identity in seen:
                raise ValueError("ID đoạn transcript bị trùng hoặc không hợp lệ.")
            seen.add(identity)
            text = item.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
                raise ValueError("Lời gốc không được trống hoặc quá 20.000 ký tự.")
            try:
                start, end = float(item["start"]), float(item["end"])
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError("Mốc thời gian không hợp lệ.") from error
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + .05:
                raise ValueError("Mốc thời gian phải nằm trong thời lượng video.")
            if start >= min(end, duration):
                raise ValueError("Mốc thời gian phải nằm trong thời lượng video.")
            result.append(dict(item, id=identity, start=start, end=min(end, duration), text=text.strip()))
        return result

    @staticmethod
    def parse_srt(path):
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
        def seconds(value):
            hour, minute, second, ms = map(int, re.split(r"[:,.]", value))
            if minute > 59 or second > 59:
                raise ValueError("Mốc thời gian SRT không hợp lệ.")
            return hour * 3600 + minute * 60 + second + ms / 1000
        result = []
        for block in re.split(r"\n\s*\n", text):
            lines = block.splitlines()
            if len(lines) < 3:
                raise ValueError("Cấu trúc SRT không hợp lệ.")
            match = re.fullmatch(r"(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})", lines[1].strip())
            if not match:
                raise ValueError("Mốc thời gian SRT không hợp lệ.")
            result.append({"id": lines[0].strip(), "start": seconds(match[1]), "end": seconds(match[2]), "text": "\n".join(lines[2:]).strip()})
        return result

    def edit_source(self, job, request):
        item = self.find_segment(job, request.get("segment_id"))
        changed = self.validate_segments([dict(item, text=request.get("text"), start=request.get("start"), end=request.get("end"))], job["metadata"]["duration"])[0]
        text_changed = item["text"] != changed["text"]
        timing_changed = (item["start"], item["end"]) != (changed["start"], changed["end"])
        if not text_changed and not timing_changed:
            return
        item.update(text=changed["text"], start=changed["start"], end=changed["end"], source_manual=True)
        item.setdefault("edits", []).append({"kind": "source", "created": time.time(), "revision": job.get("revision", 0) + 1})
        if text_changed:
            invalidate(job, "translate", translations=True)
            for segment in job["segments"]:
                segment["translation_needs_review"] = True
        else:
            invalidate(job, "align")
        output = self.jobs.directory(job["id"]) / "03-transcript" / f"transcript-edit-{uuid.uuid4().hex[:12]}.json"
        write_segments(output, job["segments"], job["stages"]["transcribe"].get("key"))
        job["artifacts"]["transcript"] = str(output)
        job["stages"]["transcribe"].update(status="ready", manual=True)
        if job.get("translation_key"):
            self.persist_translation(job)
        job["status"] = "edited"

    def update_project(self, job, request):
        from .pipeline import job_options
        before_options = dict(job["options"])
        before_settings = dict(job.get("settings", {}))
        options = job_options(dict(before_options, **(request.get("options") or {})))
        settings = dict(before_settings, **(request.get("settings") or {}))
        for field in ("hardware_mode", "asr_device", "translation_device", "tts_device"):
            if field in settings and settings[field] not in ("auto", "cpu", "gpu"):
                raise ValueError("Thiết bị xử lý không hợp lệ.")
        mode = request.get("mode", job["mode"])
        if mode not in ("auto", "manual"):
            raise ValueError("Chế độ xử lý không hợp lệ.")
        changed_options = {key for key in options if before_options.get(key) != options[key]}
        changed_settings = {key for key in settings if before_settings.get(key, self.settings.get(key)) != settings[key]}
        job.update(options=options, settings=settings, mode=mode)
        if "audio_track" in changed_options:
            job.pop("audio_revision", None)
            invalidate(job, "extract", translations=True)
        elif "source_language" in changed_options or changed_settings & {"whisper_model", "asr_device", "hardware_mode"}:
            invalidate(job, "transcribe", translations=True)
        elif changed_options & {"style", "glossary"} or changed_settings & {"ollama_model", "translation_device"}:
            invalidate(job, "translate", translations=True)
        elif changed_options & {"voice", "speed"} or changed_settings & {"tts_model_path", "tts_device"}:
            invalidate(job, "synthesize")
        elif "alignment" in changed_settings:
            invalidate(job, "align")
        elif changed_settings & {"output_format", "output_dir"}:
            invalidate(job, "mux")
        if changed_options & {"voice", "speed"} or changed_settings & {"tts_model_path", "tts_device", "hardware_mode"}:
            for item in job["segments"]:
                item["audio_manual"] = False
                item["audio_key"] = None
        self.settings.update(settings)
        job["status"] = "edited" if changed_options or changed_settings else "ready"

    @staticmethod
    def find_segment(job, identity):
        item = next((item for item in job["segments"] if item["id"] == identity), None)
        if item is None:
            raise ValueError("Không tìm thấy đoạn lời thoại.")
        return item

    def segment_action(self, job, request):
        self.reconcile_translation(job)
        item = self.find_segment(job, request.get("segment_id"))
        action = request["action"]
        if action == "regenerate_segment":
            if not item.get("translation") or item.get("translation_stale"):
                raise ValueError("Đoạn chưa có bản dịch hợp lệ.")
            signature = self.tts_key()
            speech = providers.VieNeuSpeech(self.settings, self.data_dir, self.log)
            output = self.jobs.directory(job["id"]) / "05-voice" / f"take-{uuid.uuid4().hex}.wav"
            self.speech_file(speech, item["translation"], job["options"], signature, output)
            item.update(audio_path=str(output), audio_key=digest(signature, item["translation"], job["options"]["voice"], job["options"]["speed"]), audio_manual=False, warning=None)
            invalidate(job, "align")
            if self.voices_valid(job):
                job["stages"]["synthesize"].update(status="ready", key=self.stage_key(job, "synthesize"))
        else:
            if action == "retranslate_segment":
                result = providers.OllamaTranslator(self.settings).translate(job["segments"], job["options"], self.log, target_id=item["id"])
                text = result[item["id"]]
            else:
                text = request.get("text", "")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise ValueError("Bản dịch phải có từ 1 đến 2.000 ký tự.")
            item.update(translation=text.strip(), translation_stale=False, translation_needs_review=False,
                        translation_manual=action == "edit_segment", audio_path=None, audio_key=None, audio_manual=False, warning=None)
            item.setdefault("edits", []).append({"kind": action, "created": time.time(), "revision": job.get("revision", 0) + 1})
            invalidate(job, "synthesize")
            self.persist_translation(job)
            if all(segment.get("translation") and not segment.get("translation_stale") for segment in job["segments"]):
                job["translation_identity"] = self.effective_translation_identity(job)
                job["translation_key"] = self.stage_key(job, "translate")
                job["stages"]["translate"].update(status="ready", key=job["translation_key"])
        job["status"] = "edited"
        self.save_event(job)
        self.boundary()

    def import_artifact(self, job, request):
        path = Path(request["path"])
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("Chọn tệp nhập từ đường dẫn tuyệt đối hợp lệ.")
        stage = request.get("stage")
        if stage not in ("extract", "transcribe", "translate", "synthesize"):
            raise ValueError("Bước nhập không hợp lệ.")
        if not job.get("metadata"):
            raise ValueError("Cần chạy bước đọc video trước khi nhập kết quả.")
        if stage in ("translate", "synthesize"):
            self.reconcile_translation(job)
        directory = self.jobs.directory(job["id"])
        if stage in ("extract", "synthesize"):
            self.validate_wav(path, job["metadata"]["duration"] if stage == "extract" else None, source_audio=stage == "extract")
            folder = "02-audio" if stage == "extract" else "05-voice"
            output = directory / folder / f"import-{uuid.uuid4().hex}.wav"
            shutil.copy2(path, output)
            if stage == "extract":
                job["artifacts"]["source_audio"] = str(output)
                job["audio_revision"] = digest("import", fingerprint(output))
                invalidate(job, "transcribe", translations=True)
            else:
                item = self.find_segment(job, request.get("segment_id"))
                if not item.get("translation") or item.get("translation_stale"):
                    raise ValueError("Đoạn chưa có bản dịch hợp lệ.")
                item.update(audio_path=str(output), audio_key=digest("import", fingerprint(output)), audio_manual=True)
                invalidate(job, "align")
        else:
            if path.suffix.lower() == ".srt":
                rows = self.parse_srt(path)
            elif path.suffix.lower() == ".json":
                rows = read_json(path)
                if isinstance(rows, dict):
                    rows = rows.get("segments")
            else:
                raise ValueError("Chỉ nhập transcript hoặc bản dịch dạng JSON/SRT.")
            if stage == "transcribe":
                segments = self.validate_segments(rows, job["metadata"]["duration"])
                for item in segments:
                    item["source_manual"] = True
                    for key in ("translation", "audio_path", "audio_key", "translation_manual"):
                        item.pop(key, None)
                output = directory / "03-transcript" / f"import-{uuid.uuid4().hex}.json"
                write_segments(output, segments, digest("manual", segments))
                job["segments"] = segments
                job["artifacts"]["transcript"] = str(output)
                invalidate(job, "translate", translations=True)
            else:
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Bản dịch nhập không hợp lệ.")
                updates = {}
                for row in rows:
                    identity = row.get("id") if isinstance(row, dict) else None
                    item = self.find_segment(job, identity)
                    text = row.get("translation", row.get("text"))
                    if identity in updates or not isinstance(text, str) or not text.strip() or len(text) > 2000:
                        raise ValueError("ID hoặc nội dung bản dịch không hợp lệ.")
                    if path.suffix.lower() == ".srt" and (abs(row["start"] - item["start"]) > .05 or abs(row["end"] - item["end"]) > .05):
                        raise ValueError("Mốc thời gian bản dịch SRT không khớp transcript.")
                    updates[identity] = text.strip()
                for identity, text in updates.items():
                    self.find_segment(job, identity).update(translation=text, translation_manual=True, translation_stale=False, audio_path=None, audio_key=None, audio_manual=False)
                invalidate(job, "synthesize")
                job["translation_identity"] = self.effective_translation_identity(job)
                job["translation_key"] = self.stage_key(job, "translate")
                self.persist_translation(job)
        ready = True
        if stage == "translate":
            ready = all(item.get("translation") and not item.get("translation_stale") for item in job["segments"])
        if stage == "synthesize":
            ready = self.voices_valid(job)
        job["stages"][stage].update(status="ready" if ready else "stale", manual=stage in ("extract", "transcribe"), key=self.stage_key(job, stage))
        job["revision"] = job.get("revision", 0) + 1
        job["status"] = "edited"
