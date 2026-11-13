import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubbing import providers, system_check
from dubbing.jobs import Jobs, write_json
from dubbing.pipeline import Engine


class DevProfileTests(unittest.TestCase):
    def test_stored_project_paths_cannot_override_dev_profile(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp) / ".dev"
            engine = Engine(root, {"dev_mode": True, "dev_root": str(root), "ollama_owned": True}, None, lambda _: None)
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"source")
            job = engine.jobs.create(source, {}, settings={
                "dev_mode": False, "dev_root": r"C:\old", "ffmpeg_path": r"C:\old\ffmpeg.exe",
                "ffprobe_path": r"C:\old\ffprobe.exe", "tts_model_path": r"C:\old\models",
                "projects_dir": r"C:\old\projects", "output_dir": r"C:\old\output", "ollama_host": "https://cloud.example",
                "ollama_owned": False, "tts_device": "cpu",
            })
            with engine.working(job):
                self.assertTrue(engine.settings["dev_mode"])
                self.assertEqual(engine.settings["dev_root"], str(root))
                self.assertEqual(engine.settings["ffmpeg_path"], str(root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"))
                self.assertEqual(engine.settings["ffprobe_path"], str(root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"))
                self.assertEqual(engine.settings["tts_model_path"], str(root / "models" / "vieneu"))
                self.assertEqual(engine.settings["projects_dir"], str(root / "projects"))
                self.assertEqual(engine.settings["output_dir"], str(root / "output"))
                self.assertEqual(engine.settings["ollama_host"], "127.0.0.1:11435")
                self.assertTrue(engine.settings["ollama_owned"])
                self.assertEqual(engine.settings["tts_device"], "cpu")
                self.assertEqual(engine.media.settings, engine.settings)
            self.assertEqual(engine.jobs.projects_dir, root / "projects")

    def test_environment_dev_mode_cannot_be_disabled_by_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".dev"
            with patch.dict(os.environ, {"AIVD_DEV_MODE": "1", "AIVD_DEV_ROOT": str(root)}, clear=True):
                engine = Engine(root, {"dev_mode": False, "projects_dir": r"C:\old\projects"}, None, lambda _: None)
            self.assertEqual(engine.jobs.projects_dir, root / "projects")
            self.assertTrue(engine.settings["dev_mode"])

    def test_dev_root_environment_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "AIVD_DEV_MODE": "1", "AIVD_DEV_ROOT": str(Path(temp) / "other"),
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "AIVD_DEV_ROOT"):
                Engine(Path(temp) / ".dev", {}, None, lambda _: None)

    def test_dev_and_release_http_connections_use_distinct_fixed_loopback_ports(self):
        for settings, environment, expected in (
            ({}, {"OLLAMA_HOST": "https://cloud.example"}, 11434),
            ({"dev_mode": True, "ollama_owned": True}, {}, 11435),
            ({"ollama_owned": True}, {"AIVD_DEV_MODE": "1", "OLLAMA_HOST": "http://127.0.0.1:11435"}, 11435),
        ):
            with self.subTest(expected=expected), patch.dict(os.environ, environment, clear=True), \
                 patch.object(providers.http.client, "HTTPConnection") as connection:
                response = connection.return_value.getresponse.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = b'{"models":[]}'
                self.assertEqual(providers.ollama_models(settings), [])
                self.assertEqual(connection.call_args.args, ("127.0.0.1", expected))

    def test_dev_rejects_remote_ambiguous_or_other_port_hosts_before_connecting(self):
        for host in ("https://cloud.example", "127.0.0.1:11434", "localhost:11435",
                     "http://127.0.0.1:11435/api", "http://user@127.0.0.1:11435", "https://127.0.0.1:11435"):
            with self.subTest(host=host), patch.dict(os.environ, {"OLLAMA_HOST": host}, clear=True), \
                 patch.object(providers.http.client, "HTTPConnection") as connection:
                with self.assertRaisesRegex(ValueError, "127.0.0.1:11435"):
                    providers.ollama_models({"dev_mode": True, "ollama_owned": True})
                connection.assert_not_called()

    def test_dev_http_refuses_unowned_daemon_before_constructing_connection(self):
        for owned in (None, False, "true"):
            with self.subTest(owned=owned), patch.dict(os.environ, {}, clear=True), \
                 patch.object(providers.http.client, "HTTPConnection") as connection:
                response = connection.return_value.getresponse.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = b'{}'
                with self.assertRaises(providers.OllamaUnavailable):
                    providers.ollama_request("/api/chat", {"messages": ["private transcript"]},
                                             settings={"dev_mode": True, "ollama_owned": owned})
                connection.assert_not_called()

    def test_unowned_dev_translation_reuses_complete_cache_without_http(self):
        segments = [{"id": "1", "start": 0, "end": 1, "text": "Hello"}]
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(providers.http.client, "HTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"models":[]}'
            translator = providers.OllamaTranslator({"dev_mode": True, "ollama_owned": False})
            self.assertEqual(translator.translate(segments, {}, lambda *_: None,
                                                 existing={"1": "Chào"}), {"1": "Chào"})
            with self.assertRaises(providers.OllamaUnavailable):
                translator.translate(segments, {}, lambda *_: None)
            connection.assert_not_called()

    def test_dev_cannot_open_external_project_or_rewrite_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            directory = Path(temp)
            source = directory / "source.mp4"
            source.write_bytes(b"source")
            external = Jobs(directory / "old-app-data")
            job = external.create(source, {})
            project = external.directory(job["id"])
            manifest = project / "project.json"
            before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
            root = directory / ".dev"
            engine = Engine(root, {"dev_mode": True}, None, lambda _: None)
            with self.assertRaisesRegex(ValueError, r"\.dev/projects"):
                engine.dispatch({"action": "open_project", "path": str(project)})
            self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), before)
            self.assertFalse(engine.jobs.index_path.exists())

    def test_dev_rejects_external_registered_project_before_access_or_save(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            directory = Path(temp)
            source = directory / "source.mp4"
            source.write_bytes(b"source")
            external = Jobs(directory / "old-app-data")
            job = external.create(source, {})
            project = external.directory(job["id"])
            manifest = project / "project.json"
            before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
            engine = Engine(directory / ".dev", {"dev_mode": True}, None, lambda _: None)
            write_json(engine.jobs.index_path, {"version": 1, "projects": {job["id"]: str(project)}})
            for operation in (lambda: engine.jobs.directory(job["id"]), lambda: engine.jobs.load(job["id"]),
                              lambda: engine.jobs.save(job), lambda: engine.jobs.register(project, job["id"])):
                with self.assertRaisesRegex(ValueError, r"\.dev/projects"):
                    operation()
            self.assertEqual(engine.jobs.list(), [])
            self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), before)

    def test_dev_projects_remain_local_when_the_selected_source_is_external(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            directory = Path(temp)
            source = directory / "outside-source.mp4"
            source.write_bytes(b"source")
            root = directory / ".dev"
            engine = Engine(root, {"dev_mode": True}, None, lambda _: None)
            project = engine.dispatch({"action": "create_project", "source": str(source)})
            self.assertEqual(project["source"], str(source))
            self.assertTrue(Path(project["directory"]).is_relative_to(root / "projects"))

    def test_check_does_not_query_or_report_ready_against_unowned_dev_daemon(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
             patch.object(system_check, "_inventory", return_value={}), \
             patch.object(providers, "ollama_models", side_effect=AssertionError("foreign daemon")):
            result = system_check.check({"dev_mode": True}, Path(temp))
        status = {item["name"]: item for item in result["checks"]}
        self.assertFalse(result["ready"])
        self.assertEqual(status["Ollama"]["status"], "missing")
        self.assertIn("11435", status["Ollama"]["detail"])
