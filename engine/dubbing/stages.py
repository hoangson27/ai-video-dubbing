"""Durable stage execution shared by automatic and manual workflows."""
import shutil
import time
import uuid
import wave
from pathlib import Path

from . import providers
from .alignment import align, frame_count
from .jobs import STAGES, digest, fingerprint, read_json, write_json


def model_signature(directory, paths):
    return [fingerprint(path) for name in paths if (path := directory / name).is_file()]


def invalidate(job, stage, *, translations=False):
    for name in STAGES[STAGES.index(stage):]:
        record = job["stages"][name]
        record["status"] = "stale" if record.get("key") or record["status"] != "pending" else "pending"
        record.pop("error", None)
    if translations:
        for item in job["segments"]:
            if not item.get("translation_manual"):
                item["translation_stale"] = True
    job["output"] = None
    job["revision"] = job.get("revision", 0) + 1


def write_segments(path, segments, key):
    fields = ("id", "start", "end", "text", "translation", "source_manual", "translation_manual", "translation_stale")
    write_json(path, {"key": key, "segments": [{key: item[key] for key in fields if key in item} for item in segments]})
    def stamp(value):
        milliseconds = round(value * 1000)
        seconds, ms = divmod(milliseconds, 1000)
        minutes, second = divmod(seconds, 60)
        hour, minute = divmod(minutes, 60)
        return f"{hour:02}:{minute:02}:{second:02},{ms:03}"
    translated = path.parent.name == "04-translation"
    srt = "\n\n".join(f"{index}\n{stamp(item['start'])} --> {stamp(item['end'])}\n{item.get('translation', '') if translated else item['text']}" for index, item in enumerate(segments, 1))
    temporary = path.with_suffix(".part.srt")
    temporary.write_text(srt + "\n", encoding="utf-8")
    temporary.replace(path.with_suffix(".srt"))


class StageRunner:
    def effective_translation_identity(self, job):
        current = providers.translation_identity(self.settings)
        saved = job.get("translation_identity")
        # Missing weights/service cannot disprove the receipt for completed work.
        # A changed known digest, configured model, provider or prompt always wins.
        if current.get("digest") is None and isinstance(saved, dict) and saved.get("digest"):
            if all(current.get(field) == saved.get(field) for field in ("provider", "model", "prompt_version")):
                return dict(saved)
        return current

    @staticmethod
    def translation_signature(job, identity):
        return digest("translation-v3", [(item["id"], item["text"]) for item in job["segments"]],
                      job["options"]["style"], job["options"]["glossary"], identity)

    def stage_key(self, job, stage):
        options = job["options"]
        records = job["stages"]
        if stage == "probe":
            return digest("probe-v1", job.get("source_sha256", job["fingerprint"]))
        if stage == "extract":
            return digest("extract-v1", records["probe"].get("key"), options.get("audio_track"), job.get("audio_revision"))
        if stage == "transcribe":
            model = providers.whisper_model_path(self.settings, self.data_dir)
            return digest("asr-v3", records["extract"].get("key"), options["source_language"], str(model),
                          model_signature(model, providers.WHISPER_FILES), providers.WHISPER_VERSION,
                          providers.CT2_VERSION, providers.device_mode(self.settings, "asr"))
        if stage == "translate":
            return self.translation_signature(job, self.effective_translation_identity(job))
        if stage == "synthesize":
            return digest("speech-v2", self.tts_key(), options["voice"], options["speed"],
                          [(item["id"], item.get("translation")) for item in job["segments"]])
        if stage == "align":
            return digest("align-v2", [(item["id"], item["start"], item["end"], item.get("audio_key"), item.get("audio_path")) for item in job["segments"]], job["metadata"]["duration"], self.settings.get("alignment"))
        return digest("mux-v2", records["align"].get("key"), self.settings.get("output_format", "auto"), job.get("source_sha256", job["fingerprint"]))

    def valid_stage(self, job, stage):
        record = job["stages"][stage]
        if record["status"] != "ready":
            return False
        label = {"extract": "source_audio", "transcribe": "transcript", "translate": "translation", "align": "dubbed"}.get(stage)
        if label and not Path(job["artifacts"].get(label, "")).is_file():
            return False
        if stage == "synthesize" and not self.voices_valid(job):
            return False
        if stage == "mux" and (not job.get("output") or not Path(job["output"]).is_file()):
            return False
        return record.get("manual") or record.get("key") == self.stage_key(job, stage)

    def voices_valid(self, job):
        signature = self.tts_key()
        for item in job["segments"]:
            expected = digest(signature, item.get("translation"), job["options"]["voice"], job["options"]["speed"])
            if not item.get("audio_path") or (not item.get("audio_manual") and item.get("audio_key") != expected):
                return False
            try:
                if not frame_count(Path(item["audio_path"])):
                    return False
            except (OSError, ValueError, wave.Error, EOFError):
                return False
        return bool(job["segments"])

    def run_one(self, job, stage, force=False, verify_packets=False):
        if stage not in STAGES:
            raise ValueError("Bước xử lý không hợp lệ.")
        self.boundary()
        if not force and self.valid_stage(job, stage):
            return
        prerequisites = {"probe": (), "extract": ("probe",), "transcribe": ("extract",),
                         "translate": ("transcribe",), "synthesize": ("translate",),
                         "align": ("probe", "synthesize"), "mux": ("probe", "align")}
        for prerequisite in prerequisites[stage]:
            if not self.valid_stage(job, prerequisite):
                raise ValueError(f"Cần chạy bước {prerequisite} trước bước {stage}.")
        # A rerun never discards source edits without an explicit new import.
        if stage == "transcribe" and any(item.get("source_manual") for item in job["segments"]):
            raise ValueError("Lời gốc có chỉnh sửa thủ công. Nhập transcript mới để thay thế có chủ đích.")
        invalidate(job, stage)
        record = job["stages"][stage]
        record.update(status="running", error=None)
        record.pop("manual", None)
        job["stage"] = stage
        self.save_event(job)
        started = time.monotonic()
        try:
            getattr(self, "stage_" + stage)(job, force=force, verify_packets=verify_packets)
            record.update(status="ready", key=job["translation_key"] if stage == "translate" else self.stage_key(job, stage), error=None,
                          duration_ms=round((time.monotonic() - started) * 1000))
            job["revision"] = job.get("revision", 0) + 1
            self.save_event(job)
            self.boundary()
        except Exception as error:
            from .pipeline import Paused
            if isinstance(error, Paused):
                if record["status"] != "ready":
                    record["status"] = "paused"
            else:
                record.update(status="failed", error=str(error), duration_ms=round((time.monotonic() - started) * 1000))
            raise

    def run(self, job, verify_packets=False, *, stage=None, force=False, stop_after=None, mode=None):
        if mode is not None:
            if mode not in ("auto", "manual"):
                raise ValueError("Chế độ xử lý không hợp lệ.")
            job["mode"] = mode
        if stop_after is not None and stop_after not in STAGES:
            raise ValueError("Bước dừng không hợp lệ.")
        stages = (stage,) if stage else STAGES
        if not stage and not force and job["stages"]["transcribe"].get("manual") and self.valid_stage(job, "transcribe"):
            stages = tuple(name for name in STAGES if name != "extract")
        if not stage and job["mode"] == "manual":
            stages = (next((name for name in stages if not self.valid_stage(job, name)), "mux"),)
        for name in stages:
            self.run_one(job, name, force=force, verify_packets=verify_packets)
            if name == stop_after:
                break
        job.update(status="completed" if self.valid_stage(job, "mux") else "ready", error=None)
        self.save_event(job)

    def stage_probe(self, job, **_):
        self.progress("probe", 3, "Đọc thông tin video và luồng âm thanh.")
        metadata = self.media.probe(self.source_for(job))
        if not metadata["audio_tracks"]:
            raise ValueError("Video không có luồng âm thanh.")
        if metadata["duration"] * 96_000 >= 2**32 - 44:
            raise ValueError("Video quá dài đối với định dạng WAV PCM 48 kHz.")
        selected = job["options"].get("audio_track")
        if selected is not None and selected not in [item["index"] for item in metadata["audio_tracks"]]:
            raise ValueError("Luồng âm thanh đã chọn không tồn tại.")
        job["metadata"] = metadata
        path = self.jobs.directory(job["id"]) / "01-source" / "metadata.json"
        write_json(path, metadata)
        job["artifacts"]["metadata"] = str(path)

    def stage_extract(self, job, **_):
        self.progress("extract", 10, "Tách âm thanh nguồn để nhận dạng và nghe lại.")
        directory = self.jobs.directory(job["id"])
        output = directory / "02-audio" / f"source-{uuid.uuid4().hex[:12]}.wav"
        part = output.with_suffix(".part.wav")
        metadata = dict(job["metadata"])
        selected = job["options"].get("audio_track")
        if selected is not None:
            metadata["audio_tracks"] = [item for item in metadata["audio_tracks"] if item["index"] == selected]
        try:
            self.media.extract(self.source_for(job), metadata, part)
            self.checkpoint()
            self.validate_wav(part, job["metadata"]["duration"], source_audio=True)
            part.replace(output)
        finally:
            part.unlink(missing_ok=True)
        job["artifacts"]["source_audio"] = str(output)
        job.pop("audio_revision", None)

    def stage_transcribe(self, job, force=False, **_):
        self.progress("transcribe", 20, "Nhận dạng lời thoại bằng faster-whisper.")
        key = self.stage_key(job, "transcribe")
        cached = self.transcript_cache / f"{key}.json"
        segments = None
        if not force and cached.is_file():
            data = read_json(cached)
            if data.get("key") == key:
                segments = data.get("segments")
        if segments is None:
            segments = providers.transcribe(Path(job["artifacts"]["source_audio"]), self.settings,
                                            job["options"]["source_language"], self.data_dir, self.log)
        self.checkpoint()
        segments = self.validate_segments(segments, job["metadata"]["duration"])
        write_json(cached, {"key": key, "segments": segments})
        output = self.jobs.directory(job["id"]) / "03-transcript" / f"transcript-{uuid.uuid4().hex[:12]}.json"
        write_segments(output, segments, key)
        job["segments"] = segments
        job["artifacts"]["transcript"] = str(output)

    def persist_translation(self, job, output=None):
        directory = self.jobs.directory(job["id"])
        output = output or directory / "04-translation" / f"translation-{uuid.uuid4().hex[:12]}.json"
        write_segments(output, job["segments"], job.get("translation_key"))
        job["artifacts"]["translation"] = str(output)

    def stage_translate(self, job, force=False, **_):
        identity = self.effective_translation_identity(job)
        key = self.translation_signature(job, identity)
        old_key = job.get("translation_key")
        if old_key != key:
            for item in job["segments"]:
                if not item.get("translation_manual"):
                    item["translation_stale"] = True
        job["translation_key"] = key
        job["translation_identity"] = identity
        existing = {item["id"]: item["translation"] for item in job["segments"]
                    if item.get("translation") and (item.get("translation_manual") or (not force and not item.get("translation_stale")))}
        record = job["stages"]["translate"]
        record.update(completed=len(existing), total=len(job["segments"]))
        ids = {item["id"]: item for item in job["segments"]}
        output = self.jobs.directory(job["id"]) / "04-translation" / f"translation-{uuid.uuid4().hex[:12]}.json"

        def accepted(batch):
            if not isinstance(batch, dict) or any(identity not in ids or not isinstance(text, str) or not text.strip() for identity, text in batch.items()):
                raise ValueError("Nhóm bản dịch không hợp lệ.")
            for identity, text in batch.items():
                item = ids[identity]
                if item.get("translation_manual"):
                    continue
                if text != item.get("translation"):
                    item.update(audio_path=None, audio_key=None, audio_manual=False, warning=None)
                item.update(translation=text.strip(), translation_stale=False)
                existing[identity] = text.strip()
            record["completed"] = len(existing)
            self.persist_translation(job, output)
            self.save_event(job)
            self.progress("translate", 38 + 12 * len(existing) / max(1, len(ids)), f"Đã dịch {len(existing)}/{len(ids)} đoạn.")
            self.boundary()

        self.progress("translate", 38, f"Tiếp tục dịch {len(existing)}/{len(ids)} đoạn đã lưu.")
        result = providers.OllamaTranslator(self.settings).translate(job["segments"], job["options"], self.log,
                                                                   existing=existing, on_batch=accepted)
        accepted(result)
        if len(existing) != len(ids):
            raise ValueError("Còn đoạn chưa có bản dịch hợp lệ; các nhóm thành công đã được lưu.")

    def tts_key(self):
        model = providers.tts_model_path(self.settings, self.data_dir)
        files = [f"onnx_update/{file}" for file in providers.TTS_ONNX_FILES]
        files.extend(f"update/{file}" for file in providers.TTS_GPU_FILES)
        files += ["denoiser.onnx", "speaker_encoder.onnx", "voices_v3_turbo.json"]
        return digest("tts-v2", str(model), model_signature(model, files), providers.VIENEU_VERSION,
                      model_signature(self.data_dir / "models" / "vieneu-codec-gpu", providers.TTS_GPU_CODEC_FILES),
                      providers.device_mode(self.settings, "tts"))

    def speech_file(self, speech, text, options, signature, output=None):
        key = digest(signature, text, options["voice"], options["speed"])
        output = output or self.speech_cache / f"tts-{key}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            if frame_count(output) > 0:
                return output
        except (OSError, ValueError, EOFError, wave.Error):
            pass
        raw = output.with_suffix(".raw.part.wav")
        part = output.with_suffix(".part.wav")
        try:
            self.checkpoint()
            speech.synthesize(text, options["voice"], raw)
            self.checkpoint()
            self.media.retime(raw, part, options["speed"])
            self.checkpoint()
            if not frame_count(part):
                raise RuntimeError("VieNeu-TTS tạo tệp âm thanh rỗng.")
            part.replace(output)
        finally:
            raw.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        return output

    def stage_synthesize(self, job, force=False, **_):
        signature = self.tts_key()
        speech = None
        directory = self.jobs.directory(job["id"]) / "05-voice"
        total = len(job["segments"])
        last_event = time.monotonic()
        for index, item in enumerate(job["segments"]):
            key = digest(signature, item["translation"], job["options"]["voice"], job["options"]["speed"])
            current = Path(item["audio_path"]) if item.get("audio_path") else None
            valid = False
            if current and (item.get("audio_key") == key or item.get("audio_manual")) and not force:
                try:
                    valid = frame_count(current) > 0
                except (OSError, ValueError, wave.Error, EOFError):
                    pass
            if not valid:
                cached = self.speech_cache / f"tts-{key}.wav"
                cache_valid = False
                try:
                    cache_valid = not force and frame_count(cached) > 0
                except (OSError, ValueError, wave.Error, EOFError):
                    pass
                if not cache_valid:
                    if speech is None:
                        speech = providers.VieNeuSpeech(self.settings, self.data_dir, self.log)
                    if force:
                        cached = directory / f"take-{uuid.uuid4().hex}.wav"
                    cached = self.speech_file(speech, item["translation"], job["options"], signature, cached)
                owned = cached
                if not cached.is_relative_to(directory):
                    owned = directory / f"voice-{uuid.uuid4().hex}.wav"
                    shutil.copy2(cached, owned)
                item.update(audio_path=str(owned), audio_key=key, audio_manual=False, warning=None)
            job["stages"]["synthesize"].update(completed=index + 1, total=total)
            self.jobs.save(job)
            if index % 10 == 0 or index + 1 == total or time.monotonic() - last_event >= 1:
                self.send({"type": "project", "data": self.jobs.view(job)})
                last_event = time.monotonic()
            self.progress("synthesize", 52 + 29 * (index + 1) / max(total, 1), f"Giọng đọc {index + 1}/{total} đoạn.")
            self.boundary()

    def stage_align(self, job, **_):
        self.progress("align", 84, "Đồng bộ lời đọc với mốc thời gian nguồn.")
        destination = self.jobs.directory(job["id"]) / "06-aligned" / f"dubbed-{uuid.uuid4().hex[:12]}.wav"
        warnings = align(job["segments"], job["metadata"]["duration"], destination, self.media.retime, self.checkpoint)
        for item in job["segments"]:
            item["warning"] = warnings.get(item["id"])
        job["warnings"] = [f"Đoạn {identity}: {value}" for identity, value in warnings.items()]
        job["artifacts"]["dubbed"] = str(destination)
        write_json(destination.with_suffix(".json"), {"warnings": warnings, "timeline": [{"id": item["id"], "start": item["start"], "end": item["end"]} for item in job["segments"]]})

    def stage_mux(self, job, verify_packets=False, **_):
        self.progress("mux", 94, "Ghép âm thanh với luồng hình gốc bằng stream copy.")
        output = self.publish(job, self.source_for(job), job["metadata"], Path(job["artifacts"]["dubbed"]), verify_packets)
        job["output"] = str(output)
        job["exports"].append({"path": str(output), "created": time.time(), "revision": job.get("revision", 0),
                               "stages": {name: record.get("key") for name, record in job["stages"].items()}})

    def publish(self, job, source, metadata, dubbed, verify_packets):
        directory = self.jobs.directory(job["id"])
        output_dir = directory / "07-output" if self.jobs.is_project(job) else self.data_dir / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        configured = self.settings.get("output_format", "auto")
        suffixes = ("." + configured,) if configured in ("mp4", "mkv") else (".mp4", ".mkv")
        for suffix in suffixes:
            self.checkpoint()
            temporary = output_dir / f".dub-{job['id']}-{uuid.uuid4().hex}{suffix}"
            job["pending_output"] = str(temporary)
            self.jobs.save(job)
            try:
                self.media.mux(source, metadata, dubbed, temporary)
            except RuntimeError as error:
                temporary.unlink(missing_ok=True)
                errors.append(f"{suffix[1:].upper()} · {error}")
                if suffix == ".mp4":
                    self.log("warning", "Không ghép được MP4 với luồng hình gốc. Đang thử MKV.")
                continue
            if verify_packets:
                self.log("info" if self.media.packet_digest(source) == self.media.packet_digest(temporary) else "warning", "Đã kiểm tra SHA-256 payload video.")
            self.source_for(job)
            self.checkpoint()
            revision = len(job.get("exports", [])) + 1
            output = output_dir / f"{source.stem[:70]} - VI - v{revision:03}{suffix}"
            while output.exists():
                revision += 1
                output = output_dir / f"{source.stem[:70]} - VI - v{revision:03}{suffix}"
            temporary.rename(output)
            job.pop("pending_output", None)
            return output.resolve()
        raise RuntimeError("Không thể xuất video bằng stream copy. " + " | ".join(errors))

    def export(self, job, verify_packets=False):
        if not self.valid_stage(job, "translate"):
            raise ValueError("Bản dịch chưa hợp lệ. Hãy chạy bước dịch trước khi xuất.")
        for stage in ("synthesize", "align", "mux"):
            self.run_one(job, stage, force=stage == "mux", verify_packets=verify_packets)
        job.update(status="completed", error=None)
        self.save_event(job)
