"""Project persistence. Loading and listing never repair or write user data."""
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STAGES = ("probe", "extract", "transcribe", "translate", "synthesize", "align", "mux")
FOLDERS = ("01-source", "02-audio", "03-transcript", "04-translation", "05-voice", "06-aligned", "07-output", "logs")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    contents = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    complete = False
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".pending.json", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        complete = True  # The closed candidate is valid JSON; never truncate the previous manifest.
        delays = (0.025, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8)
        for attempt in range(len(delays) + 1):
            try:
                temporary.replace(path)
                return
            except OSError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33):
                    raise
                if attempt == len(delays):
                    raise PermissionError(
                        f"Không lưu được dữ liệu vào {path}. Tệp đang bị khóa hoặc Windows từ chối quyền ghi. "
                        f"Đã giữ bản lưu mới tại {temporary}. "
                        "Hãy đóng ứng dụng đang giữ tệp hoặc kiểm tra quyền thư mục rồi thử lại."
                    ) from error
                time.sleep(delays[attempt])
    finally:
        if temporary is not None and not complete:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A cleanup lock must not hide the original write/disk error.


def digest(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def fingerprint(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino]


def file_digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def copy_verified(source, destination):
    shutil.copy2(source, destination)
    if file_digest(Path(source)) != file_digest(Path(destination)):
        raise OSError("Không xác minh được bản sao dữ liệu dự án.")


def apply_translation(job, segments, key):
    previous = {item["id"]: item for item in job.get("segments", [])}
    job["segments"] = [previous.get(item["id"], item) if previous.get(item["id"], {}).get("translation") == item.get("translation") else item for item in segments]
    job["translation_key"] = key


def safe_tree(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (candidate.exists() and getattr(candidate.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Thư mục dự án không được chứa liên kết hoặc junction.")


@contextmanager
def exclusive(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "engine.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Một tác vụ khác đang sử dụng Python Engine.") from error
        yield


class Jobs:
    def __init__(self, data_dir, projects_dir=None, *, allowed_root=None):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "jobs"
        self.cancel_dir = self.data_dir / "cancel"
        self.projects_dir = Path(projects_dir) if projects_dir else self.data_dir / "projects"
        self.index_path = self.data_dir / "project-index.json"
        self.allowed_root = Path(allowed_root).resolve() if allowed_root is not None else None

    def require_local_directory(self, path):
        if self.allowed_root is not None and (not path.is_absolute() or not path.resolve().is_relative_to(self.allowed_root)):
            raise ValueError("Chế độ dev chỉ mở và ghi dự án trong .dev. Hãy sao chép thư mục dự án vào .dev/projects rồi mở lại.")

    def index(self):
        if not self.index_path.is_file():
            return {"version": 1, "projects": {}}
        index = read_json(self.index_path)
        if index.get("version") != 1 or not isinstance(index.get("projects"), dict):
            raise ValueError("Chỉ mục dự án không hợp lệ.")
        return index

    @staticmethod
    def validate_id(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Mã tác vụ không hợp lệ.")

    def directory(self, job_id):
        self.validate_id(job_id)
        index = self.index()
        if job_id in index.get("forgotten", []):
            raise ValueError("Dự án đã được gỡ khỏi danh sách. Mở lại thư mục dự án để đăng ký.")
        registered = index["projects"].get(job_id)
        if registered:
            path = Path(registered)
            if not path.is_absolute():
                raise ValueError("Đường dẫn dự án không hợp lệ.")
            self.require_local_directory(path)
            safe_tree(path)
            marker = read_json(path / ".dubbing-project")
            if marker != {"version": 1, "id": job_id}:
                raise ValueError("Không xác minh được chủ sở hữu thư mục dự án.")
            return path.resolve()
        path = self.root / job_id
        self.require_local_directory(path)
        safe_tree(path)
        return path

    def is_project(self, job):
        return bool(job.get("schema_version"))

    def register(self, directory, job_id):
        self.require_local_directory(directory)
        safe_tree(directory)
        index = self.index()
        index["forgotten"] = [identity for identity in index.get("forgotten", []) if identity != job_id]
        index["projects"][job_id] = str(directory.resolve())
        write_json(self.index_path, index)

    def _new_directory(self, source, job_id):
        if not self.projects_dir.is_absolute():
            raise ValueError("Thư mục dự án phải là đường dẫn tuyệt đối.")
        self.require_local_directory(self.projects_dir)
        safe_tree(self.projects_dir)
        for ancestor in (self.projects_dir, *self.projects_dir.parents):
            if (ancestor / ".dubbing-project").exists():
                raise ValueError("Không tạo dự án bên trong một dự án khác.")
        for registered in self.index()["projects"].values():
            if self.projects_dir.resolve().is_relative_to(Path(registered).resolve()):
                raise ValueError("Không tạo dự án bên trong một dự án khác.")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", source.stem).strip(" .")[:70] or "Video"
        directory = self.projects_dir / f"{title}-{job_id[:8]}"
        if directory.exists():
            directory = self.projects_dir / f"{title}-{job_id[:8]}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True)
        for name in FOLDERS:
            (directory / name).mkdir()
        write_json(directory / ".dubbing-project", {"version": 1, "id": job_id})
        return directory

    def create(self, source, options, *, settings=None, copy_source=False, new_project=False):
        original = fingerprint(source)
        if not new_project:
            for view in self.list():
                job = self.load(view["id"])
                if job.get("original_fingerprint", job.get("fingerprint")) == original:
                    return job
        job_id = uuid.uuid4().hex
        directory = self._new_directory(source, job_id)
        selected = source
        source_hash = file_digest(source)
        if copy_source:
            selected = directory / "01-source" / source.name
            shutil.copy2(source, selected)
            if file_digest(selected) != source_hash:
                raise OSError("Không xác minh được bản sao video nguồn.")
        job = {
            "schema_version": 1, "id": job_id, "title": source.stem, "source": str(selected),
            "source_mode": "copy" if copy_source else "reference", "fingerprint": fingerprint(selected),
            "original_fingerprint": original, "options": options, "settings": settings or {},
            "source_sha256": source_hash,
            "status": "ready", "stage": "probe", "mode": "auto", "updated": time.time(),
            "revision": 0, "segments": [], "warnings": [], "output": None, "error": None,
            "metadata": None, "stages": {stage: {"status": "pending"} for stage in STAGES},
            "artifacts": {}, "exports": [],
        }
        self.register(directory, job_id)
        self.save(job)
        return job

    def _paths(self, job, directory, store):
        job = copy.deepcopy(job)

        def convert(value, external=False):
            if not value:
                return value
            path = Path(value)
            if store:
                if path.is_absolute():
                    safe_tree(path)
                    if path.resolve().is_relative_to(directory):
                        return path.resolve().relative_to(directory).as_posix()
                    if external:
                        return str(path)
                    raise ValueError("Artifact phải nằm trong thư mục dự án.")
                resolved = directory / path
            else:
                if path.is_absolute():
                    if external:
                        return str(path)
                    raise ValueError("Artifact dự án phải lưu bằng đường dẫn tương đối.")
                resolved = directory / path
            if not resolved.resolve().is_relative_to(directory):
                raise ValueError("Artifact vượt ra ngoài thư mục dự án.")
            safe_tree(resolved)
            return path.as_posix() if store else str(resolved.resolve())

        for field in ("source", "output", "pending_output"):
            if job.get(field):
                job[field] = convert(job[field], field == "source" and job.get("source_mode") != "copy")
        if job.get("source_mode") == "copy" and job.get("fingerprint"):
            job["fingerprint"][0] = convert(job["fingerprint"][0])
        for item in job.get("segments", []):
            if item.get("audio_path"):
                item["audio_path"] = convert(item["audio_path"])
        job["artifacts"] = {key: convert(value) for key, value in job.get("artifacts", {}).items()}
        for item in job.get("exports", []):
            item["path"] = convert(item["path"])
        return job

    def save(self, job):
        job["updated"] = time.time()
        directory = self.directory(job["id"])
        project = self.is_project(job)
        write_json(directory / ("project.json" if project else "job.json"), self._paths(job, directory, True) if project else job)

    def load(self, job_id):
        directory = self.directory(job_id)
        path = directory / ("project.json" if (directory / ".dubbing-project").is_file() else "job.json")
        if not path.is_file():
            raise ValueError("Không tìm thấy tác vụ đã lưu.")
        job = read_json(path)
        if job.get("id") != job_id:
            raise ValueError("Manifest không khớp mã dự án.")
        return self._paths(job, directory, False) if self.is_project(job) else job

    def recover(self, job):
        """Called only under the engine mutation lock, never during reads."""
        if job.get("status") == "running":
            job.update(status="paused", error="Tác vụ trước đã dừng. Có thể tiếp tục từ kết quả đã lưu.")
            for record in job.get("stages", {}).values():
                if record["status"] == "running":
                    record["status"] = "paused"
            self.clean(job)

    def clean(self, job):
        directory = self.directory(job["id"])
        protected = {Path(value).resolve() for value in (
            [job.get("source"), job.get("output")] + list(job.get("artifacts", {}).values()) +
            [item.get("audio_path") for item in job.get("segments", [])] +
            [item.get("path") for item in job.get("exports", [])]) if value}
        patterns = {
            "02-audio": r"source-[0-9a-f]{12}\.part\.wav",
            "03-transcript": r"(?:transcript(?:-edit)?|import)-[0-9a-f]{12}\.(?:json\.part\.json|part\.srt)",
            "04-translation": r"translation-[0-9a-f]{12}\.(?:json\.part\.json|part\.srt)",
            "05-voice": r"take-[0-9a-f]{32}\.(?:raw\.part|part)\.wav",
            "06-aligned": r"dubbed-[0-9a-f]{12}\.(?:part\.wav|fit\.wav|json\.part\.json)",
            "07-output": rf"\.dub-{job['id']}-[0-9a-f]{{32}}\.(?:mp4|mkv)",
        }
        for name, pattern in patterns.items():
            folder = directory / name
            safe_tree(folder)
            for path in folder.glob("*"):
                if re.fullmatch(pattern, path.name) and path.resolve() not in protected and path.is_file() and not path.is_symlink():
                    path.unlink(missing_ok=True)
        pending = job.pop("pending_output", None)
        if pending:
            path = Path(pending)
            safe_tree(path)
            if path.resolve().is_relative_to(directory) and path.resolve() not in protected and re.fullmatch(patterns["07-output"], path.name):
                path.unlink(missing_ok=True)

    def log(self, job, level, message):
        directory = self.directory(job["id"])
        path = directory / "logs" / "job.log" if self.is_project(job) else directory / "job.log"
        with path.open("a", encoding="utf-8") as log:
            log.write(f"[{level}] {message}\n")

    def view(self, job, include_segments=True):
        public = copy.deepcopy(job)
        public.pop("cancel_path", None)
        public.pop("pending_output", None)
        public["directory"] = str(self.directory(job["id"]).resolve())
        public.setdefault("title", Path(job["source"]).stem)
        public.setdefault("mode", "auto")
        public.setdefault("source_mode", "reference")
        public.setdefault("metadata", None)
        public.setdefault("stages", {stage: {"status": "pending"} for stage in STAGES})
        public.setdefault("artifacts", {})
        public.setdefault("exports", [])
        public["legacy"] = not self.is_project(job)
        public["source_available"] = Path(job["source"]).is_file()
        if not include_segments:
            public["segments"] = []
        else:
            path = Path(public["directory"]) / ("logs/job.log" if self.is_project(job) else "job.log")
            if path.is_file():
                with path.open("rb") as log:
                    log.seek(max(0, path.stat().st_size - 24_000))
                    public["logs"] = log.read().decode("utf-8", errors="replace").splitlines()[-150:]
        return public

    def list(self):
        jobs = []
        index = self.index()
        ids = set(index["projects"])
        ids.update(path.parent.name for path in self.root.glob("*/job.json"))
        ids.difference_update(index.get("forgotten", []))
        for identity in ids:
            try:
                jobs.append(self.load(identity))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return [self.view(job, False) for job in sorted(jobs, key=lambda job: -job.get("updated", 0))]

    def open_project(self, directory):
        directory = Path(directory)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("Chọn thư mục dự án hợp lệ.")
        self.require_local_directory(directory)
        safe_tree(directory)
        directory = directory.resolve()
        if any((ancestor / ".dubbing-project").exists() for ancestor in directory.parents):
            raise ValueError("Không mở dự án nằm bên trong một dự án khác.")
        marker = read_json(directory / ".dubbing-project")
        job = read_json(directory / "project.json")
        self.validate_id(job.get("id"))
        if marker != {"version": 1, "id": job["id"]} or job.get("schema_version") != 1:
            raise ValueError("Không xác minh được manifest và chủ sở hữu dự án.")
        job = self._paths(job, directory, False)
        # Validate structure before registering a folder supplied from the picker.
        if not isinstance(job.get("segments"), list) or not isinstance(job.get("stages"), dict) or any(name not in job["stages"] for name in STAGES):
            raise ValueError("Cấu trúc manifest dự án không hợp lệ.")
        old = self.index()["projects"].get(job["id"])
        if old and Path(old).resolve() != directory and Path(old).is_dir():
            raise ValueError("Mã dự án đã được dùng bởi thư mục khác. Hãy gỡ bản cũ khỏi danh sách trước.")
        source = Path(job["source"])
        if job.get("source_mode") == "copy" and source.is_file():
            if not job.get("source_sha256") or file_digest(source) != job["source_sha256"]:
                raise ValueError("Video sao chép không khớp danh tính đã lưu.")
            job["fingerprint"] = fingerprint(source)
        write_json(directory / "project.json", self._paths(job, directory, True))
        self.register(directory, job["id"])
        return job

    def migrate(self, job):
        if self.is_project(job):
            return job
        old = self.directory(job["id"])
        new = self._new_directory(Path(job["source"]), job["id"])
        migrated = copy.deepcopy(job)
        # The caller holds the engine mutation lease. A persisted running status is an
        # interrupted job, not proof of another process; leave its original manifest intact.
        if migrated.get("status") == "running":
            migrated.update(status="paused", error=None)
        migrated.update(schema_version=1, source_mode="reference", mode="auto", revision=0,
                        settings={}, metadata=None, stages={stage: {"status": "pending"} for stage in STAGES},
                        artifacts={}, exports=[], provenance="legacy: thông tin model/prompt chưa xác định")
        if not migrated.get("segments"):
            for name in ("transcript.json", "translated.json"):
                if (old / name).is_file():
                    checkpoint = read_json(old / name)
                    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("segments"), list):
                        migrated["segments"] = copy.deepcopy(checkpoint["segments"])
                        break
        if (old / "translated.json").is_file():
            checkpoint = read_json(old / "translated.json")
            translated = {item.get("id"): item for item in checkpoint.get("segments", [])} if isinstance(checkpoint, dict) else {}
            for item in migrated["segments"]:
                candidate = translated.get(item.get("id"), {})
                if all(candidate.get(field) == item.get(field) for field in ("text", "start", "end")) and isinstance(candidate.get("translation"), str) and candidate["translation"].strip():
                    item.setdefault("translation", candidate["translation"])
        for name, folder, label in (("transcript.json", "03-transcript", "transcript"), ("translated.json", "04-translation", "translation"), ("dubbed.wav", "06-aligned", "dubbed")):
            if (old / name).is_file():
                destination = new / folder / name
                copy_verified(old / name, destination)
                migrated["artifacts"][label] = str(destination)
        for segment in migrated["segments"]:
            if segment.get("audio_path") and Path(segment["audio_path"]).is_file():
                destination = new / "05-voice" / f"legacy-{uuid.uuid4().hex}.wav"
                copy_verified(segment["audio_path"], destination)
                segment.update(audio_path=str(destination), audio_manual=True)
            else:
                segment["audio_path"] = None
            if segment.get("translation"):
                segment["translation_manual"] = True
        if job.get("output") and Path(job["output"]).is_file():
            destination = new / "07-output" / ("legacy-v001" + Path(job["output"]).suffix)
            copy_verified(job["output"], destination)
            migrated["output"] = str(destination)
            migrated["exports"].append({"path": str(destination), "created": time.time(), "revision": "legacy"})
        write_json(new / "project.json", self._paths(migrated, new, True))
        self.register(new, job["id"])
        return migrated
