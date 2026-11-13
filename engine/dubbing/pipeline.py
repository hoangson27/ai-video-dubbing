"""Engine protocol and mutation lifecycle. Stage work lives in focused helpers."""
import errno
import os
import re
import wave
from contextlib import contextmanager
from pathlib import Path

from . import providers
from .jobs import FOLDERS, STAGES, Jobs, digest, file_digest, fingerprint, safe_tree
from .media import Media, validate_video
from .project_edits import ProjectEdits
from .profile import sanitize_dev_settings
from .stages import StageRunner, invalidate


class Cancelled(RuntimeError):
    pass


class Paused(RuntimeError):
    pass


def describe(error):
    if isinstance(error, MemoryError):
        return "Hết bộ nhớ. Hãy đóng ứng dụng khác hoặc chọn model nhẹ hơn."
    if isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, "winerror", 0) == 112):
        return "Không đủ dung lượng đĩa. Giải phóng bộ nhớ hoặc đổi thư mục dự án."
    return str(error) or type(error).__name__


def validated_speed(value):
    speed = float(value)
    if not .85 <= speed <= 1.20:
        raise ValueError("Tốc độ đọc phải từ 0,85× đến 1,20×.")
    return speed


def job_options(options):
    language = options.get("source_language", "auto")
    if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}", language):
        raise ValueError("Ngôn ngữ nguồn không hợp lệ.")
    result = {"source_language": language, "voice": str(options.get("voice", "")).strip()[:120],
              "speed": validated_speed(options.get("speed", 1)), "style": str(options.get("style", "")).strip()[:500],
              "glossary": str(options.get("glossary", "")).strip()[:2500]}
    track = options.get("audio_track")
    if track is not None:
        if not isinstance(track, int) or isinstance(track, bool) or track < 0:
            raise ValueError("Luồng âm thanh không hợp lệ.")
        result["audio_track"] = track
    return result


class Engine(StageRunner, ProjectEdits):
    def __init__(self, data_dir, settings, cancel_path, send, pause_path=None):
        self.data_dir = Path(data_dir)
        self.settings = sanitize_dev_settings(settings, self.data_dir)
        self.media = Media(self.settings)
        self.jobs = Jobs(self.data_dir, self.settings.get("projects_dir"),
                         allowed_root=self.data_dir if self.settings.get("dev_mode") is True else None)
        self.cancel_path = Path(cancel_path) if cancel_path else None
        self.pause_path = Path(pause_path) if pause_path else None
        self.send = send
        self.job = None
        self.speech_cache = self.data_dir / "cache" / "speech"
        self.transcript_cache = self.data_dir / "cache" / "transcripts"

    def checkpoint(self):
        if self.cancel_path and self.cancel_path.is_file():
            raise Cancelled("Đã hủy tác vụ.")

    def boundary(self):
        self.checkpoint()
        if self.pause_path and self.pause_path.is_file():
            raise Paused("Đã tạm dừng sau khi lưu kết quả hiện tại.")

    def save_event(self, job):
        self.jobs.save(job)
        self.send({"type": "project", "data": self.jobs.view(job)})

    def log(self, level, message):
        self.checkpoint()
        if self.job:
            self.jobs.log(self.job, level, message)
        self.send({"type": "log", "level": level, "message": message})

    def progress(self, stage, progress, message):
        self.checkpoint()
        self.send({"type": "progress", "stage": stage, "progress": progress, "message": message})

    @contextmanager
    def working(self, job, *, prepare_source=False):
        self.jobs.recover(job)
        global_settings = self.settings
        self.settings = dict(global_settings, **job.get("settings", {}))
        if global_settings.get("dev_mode") is True:
            # A snapshot cannot turn off dev isolation or claim native daemon ownership.
            self.settings.update(dev_mode=True, dev_root=global_settings["dev_root"],
                                 ollama_owned=global_settings.get("ollama_owned", False))
        self.settings = sanitize_dev_settings(self.settings, self.data_dir)
        if isinstance(self.media, Media):
            self.media.settings = self.settings
        self.initialize_workflow(job, prepare_source=prepare_source)
        job.update(status="running", error=None, cancel_path=str(self.cancel_path) if self.cancel_path else None)
        self.jobs.save(job)
        self.job = job
        try:
            self.checkpoint()
            yield
        except Paused:
            job.update(status="paused", error=None)
        except Exception as error:
            job.update(status="cancelled" if isinstance(error, Cancelled) else "failed", error=describe(error))
            self.jobs.log(job, "warning" if isinstance(error, Cancelled) else "error", describe(error))
            raise
        finally:
            try:
                self.jobs.clean(job)
                self.save_event(job)
            finally:
                self.settings = global_settings
                if isinstance(self.media, Media):
                    self.media.settings = global_settings
                self.job = None

    def initialize_workflow(self, job, *, prepare_source=False):
        directory = self.jobs.directory(job["id"])
        for folder in FOLDERS:
            safe_tree(directory / folder)
            (directory / folder).mkdir(exist_ok=True)
        job["options"] = job_options(job.get("options", {}))
        job.setdefault("mode", "auto")
        job.setdefault("revision", 0)
        job.setdefault("settings", {})
        job.setdefault("stages", {stage: {"status": "pending"} for stage in STAGES})
        job.setdefault("artifacts", {})
        job.setdefault("exports", [])
        job.setdefault("metadata", None)
        # Legacy checkpoints remain authoritative until explicitly replaced; no model work is needed to migrate.
        if prepare_source and (not self.jobs.is_project(job) or job.get("provenance", "").startswith("legacy")):
            if job["segments"] and not job.get("legacy_prepared"):
                metadata = self.media.probe(self.source_for(job))
                job["metadata"] = metadata
                job["stages"]["probe"].update(status="ready", key=self.stage_key(job, "probe"))
                audio = job["artifacts"].get("source_audio")
                if not audio or not Path(audio).is_file():
                    self.stage_extract(job)
                job["stages"]["extract"].update(status="ready", key=self.stage_key(job, "extract"))
                from .stages import write_segments
                transcript = directory / "03-transcript" / "legacy-transcript.json"
                write_segments(transcript, job["segments"], "legacy")
                job["artifacts"]["transcript"] = str(transcript)
                job["stages"]["transcribe"].update(status="ready", manual=True, key="legacy")
                if all(item.get("translation") for item in job["segments"]):
                    for item in job["segments"]:
                        item["translation_manual"] = True
                    self.persist_translation(job)
                    job["stages"]["translate"].update(status="ready", manual=True, key="legacy")
                job["legacy_prepared"] = True

    def path(self, source):
        path = Path(source)
        if not path.is_absolute() or (os.name == "nt" and str(path).startswith("\\\\")):
            raise ValueError("Chọn video từ đường dẫn tuyệt đối trên ổ đĩa cục bộ.")
        path = path.resolve()
        validate_video(path)
        return path

    def source_for(self, job):
        source = self.path(job["source"])
        actual = fingerprint(source)
        expected = job["fingerprint"]
        if (actual[1:] != expected[1:] if job.get("source_mode") == "copy" else actual != expected):
            raise ValueError("Video nguồn đã thay đổi. Hãy chọn lại tệp và tạo dự án mới.")
        return source

    def dispatch(self, request):
        self.checkpoint()
        if request.get("pause_path"):
            self.pause_path = Path(request["pause_path"])
        action = request.get("action")
        if action == "check":
            from .system_check import check
            return check(self.settings, self.data_dir)
        if action == "list_jobs":
            return self.jobs.list()
        if action == "open_project":
            job = self.jobs.open_project(request["path"])
            self.save_event(job)
            return self.jobs.view(job)
        if action == "probe":
            return self.media.probe(self.path(request["source"]))
        if action == "prepare_preview":
            source = self.path(request["source"])
            directory = self.data_dir / "previews"
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / f"{digest('proxy-v2', fingerprint(source))}.mp4"
            if not output.is_file():
                part = output.with_suffix(".part.mp4")
                try:
                    self.media.preview(source, part)
                    self.checkpoint()
                    part.replace(output)
                finally:
                    part.unlink(missing_ok=True)
            return {"path": str(output)}
        if action == "preview_voice":
            options = job_options(request)
            speech = providers.VieNeuSpeech(self.settings, self.data_dir, self.log)
            return {"path": str(self.speech_file(speech, "Xin chào! Đây là bản nghe thử giọng đọc cho ứng dụng lồng tiếng video.", options, self.tts_key()))}
        if action == "create_project" or (action == "run" and not request.get("job_id")):
            job = self.jobs.create(self.path(request["source"]), job_options(request.get("options", {})),
                                   settings=self.settings, copy_source=bool(request.get("copy_source")),
                                   new_project=bool(request.get("new_project")))
            self.save_event(job)
            if action == "create_project":
                return self.jobs.view(job)
        elif action in {"run", "run_stage", "get_job", "export", "edit_segment", "retranslate_segment", "regenerate_segment", "update_project", "edit_source_segment", "import_artifact", "migrate_project", "relink_source"}:
            job = self.jobs.load(request.get("job_id"))
        else:
            raise ValueError("Thao tác Python Engine không hợp lệ.")
        if action == "get_job":
            return self.jobs.view(job)
        if action == "migrate_project":
            migrated = self.jobs.migrate(job)
            self.save_event(migrated)
            return self.jobs.view(migrated)
        with self.working(job, prepare_source=action in ("run", "run_stage", "export")):
            if action in ("run", "run_stage"):
                self.run(job, bool(request.get("verify_packets")), stage=request.get("stage") if action == "run_stage" else None,
                         force=bool(request.get("force")), stop_after=request.get("stop_after"), mode=request.get("mode"))
            elif action == "export":
                self.export(job, bool(request.get("verify_packets")))
            elif action == "update_project":
                self.update_project(job, request)
            elif action == "edit_source_segment":
                self.edit_source(job, request)
            elif action == "import_artifact":
                self.import_artifact(job, request)
            elif action == "relink_source":
                source = self.path(request["source"])
                if not job.get("source_sha256"):
                    raise ValueError("Dự án cũ chưa lưu SHA-256 để xác minh nguồn. Hãy tạo dự án mới cho video đã chọn.")
                if file_digest(source) != job["source_sha256"]:
                    raise ValueError("Video đã chọn không khớp nội dung nguồn của dự án.")
                job.update(source=str(source), source_mode="reference", fingerprint=fingerprint(source),
                           original_fingerprint=fingerprint(source), status="ready")
            else:
                self.segment_action(job, request)
        return self.jobs.view(job)
