import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubbing.jobs import Jobs, fingerprint, read_json, write_json
from dubbing.pipeline import Engine
from test_workflow import tone


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "video.mp4"
        self.source.write_bytes(b"video")
        self.events = []
        self.engine = Engine(self.root, {}, None, self.events.append)
        self.calls = {"tts": 0, "translate": 0}
        fixture = self

        class Tools:
            def probe(self, source):
                return {"duration": 2.0, "audio_tracks": [{"index": 1}]}

            def extract(self, source, metadata, destination):
                tone(destination, 2)

            def retime(self, source, destination, speed):
                shutil.copyfile(source, destination)

            def mux(self, source, metadata, audio, destination):
                destination.write_bytes(b"output")

        class Translator:
            def __init__(self, settings):
                pass

            def translate(self, segments, options, emit, target_id=None, *, existing=None, on_batch=None):
                fixture.calls["translate"] += 1
                result = dict(existing or {})
                for item in segments:
                    if item["id"] not in result:
                        batch = {item["id"]: "Việt " + item["id"]}
                        result.update(batch)
                        if on_batch:
                            on_batch(batch)
                return result

        class Speech:
            def __init__(self, *_):
                pass

            def synthesize(self, text, voice, destination):
                fixture.calls["tts"] += 1
                tone(destination, .1)

        self.engine.media = Tools()
        self.addCleanup(patch.stopall)
        patch.multiple("dubbing.providers", OllamaTranslator=Translator, VieNeuSpeech=Speech,
                       transcribe=lambda *_: [
                           {"id": str(i), "text": "Line " + str(i), "start": i * .5, "end": i * .5 + .4}
                           for i in range(2)], translation_identity=lambda _: {"model": "fixture"},
                       create=True).start()

    def create(self, **extra):
        return self.engine.dispatch(dict(action="create_project", source=str(self.source), options={}, **extra))

    def test_project_owns_artifacts_relative_storage_and_reopens_source(self):
        project = self.create(copy_source=True)
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        folder = Path(done["directory"])
        self.assertTrue((folder / ".dubbing-project").is_file())
        self.assertTrue((self.root / "project-index.json").is_file())
        manifest = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        self.assertFalse(Path(manifest["source"]).is_absolute())
        self.assertFalse(Path(manifest["segments"][0]["audio_path"]).is_absolute())
        for artifact in done["artifacts"].values():
            self.assertTrue(Path(artifact).is_relative_to(folder))
        shutil.rmtree(self.root / "cache")
        self.assertTrue(Path(done["segments"][0]["audio_path"]).is_file())
        self.assertEqual(self.create()["id"], done["id"])

    def test_load_and_list_do_not_recover_or_mutate_running_manifest(self):
        project = self.create()
        job = self.engine.jobs.load(project["id"])
        job["status"] = "running"
        self.engine.jobs.save(job)
        manifest = Path(project["directory"]) / "project.json"
        before = manifest.read_bytes()
        self.assertEqual(self.engine.jobs.load(job["id"])["status"], "running")
        self.engine.jobs.list()
        self.assertEqual(before, manifest.read_bytes())

    def test_manual_prerequisites_stop_after_and_pause_retains_batch(self):
        project = self.create()
        with self.assertRaisesRegex(ValueError, "bước"):
            self.engine.dispatch({"action": "run_stage", "job_id": project["id"], "stage": "translate"})
        transcript = self.engine.dispatch({"action": "run", "job_id": project["id"], "stop_after": "transcribe"})
        self.assertEqual(transcript["stages"]["transcribe"]["status"], "ready")
        pause = self.root / "pause"
        old_send = self.engine.send
        self.engine.send = lambda event: (old_send(event), pause.touch() if event["type"] == "project" and event["data"]["stages"]["translate"].get("completed") == 1 else None)
        paused = self.engine.dispatch({"action": "run", "job_id": project["id"], "pause_path": str(pause)})
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["segments"][0]["translation"], "Việt 0")
        self.assertFalse(paused["segments"][1].get("translation"))
        pause.unlink()
        self.engine.send = old_send
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.calls["translate"], 2)

    def test_edit_invalidates_only_affected_voice_and_preserves_export_revisions(self):
        project = self.create()
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        first = done["output"]
        edited = self.engine.dispatch({"action": "edit_segment", "job_id": project["id"], "segment_id": "0", "text": "Sửa"})
        self.assertIsNone(edited["output"])
        self.assertEqual(edited["stages"]["translate"]["status"], "ready")
        self.assertEqual(edited["stages"]["synthesize"]["status"], "stale")
        second = self.engine.dispatch({"action": "export", "job_id": project["id"]})
        self.assertEqual(self.calls["tts"], 3)
        self.assertTrue(Path(first).is_file())
        self.assertEqual(len(second["exports"]), 2)
        self.assertNotEqual(second["exports"][0]["revision"], second["exports"][1]["revision"])
        timed = self.engine.dispatch({"action": "edit_source_segment", "job_id": project["id"], "segment_id": "0", "text": "Line 0", "start": .05, "end": .4})
        self.assertEqual(timed["stages"]["synthesize"]["status"], "ready")
        self.assertEqual(timed["stages"]["align"]["status"], "stale")

    def test_settings_invalidation_preserves_manual_translations(self):
        project = self.create()
        self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.engine.dispatch({"action": "edit_segment", "job_id": project["id"], "segment_id": "0", "text": "Tay"})
        changed = self.engine.dispatch({"action": "update_project", "job_id": project["id"], "options": {"glossary": "Name"}})
        self.assertEqual(changed["stages"]["translate"]["status"], "stale")
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertEqual(done["segments"][0]["translation"], "Tay")

    def test_migration_copies_legacy_audio_and_never_changes_legacy_files(self):
        identity = "a" * 32
        legacy = self.root / "jobs" / identity
        legacy.mkdir(parents=True)
        external = self.root / "cache.wav"
        tone(external, .1)
        data = {"id": identity, "source": str(self.source), "fingerprint": fingerprint(self.source),
                "options": {}, "status": "edited", "stage": "synthesize", "updated": 1,
                "segments": [{"id": "1", "start": 0, "end": .4, "text": "Hi", "translation": "Chào", "audio_path": str(external)}], "warnings": [], "output": None}
        write_json(legacy / "job.json", data)
        before = (legacy / "job.json").read_bytes()
        migrated = self.engine.dispatch({"action": "migrate_project", "job_id": identity})
        self.assertNotEqual(Path(migrated["directory"]), legacy)
        self.assertTrue(Path(migrated["segments"][0]["audio_path"]).is_relative_to(Path(migrated["directory"])))
        self.assertEqual(before, (legacy / "job.json").read_bytes())

    def test_manual_mode_runs_only_next_stage_and_rejects_overwrite_of_source_edits(self):
        project = self.create()
        first = self.engine.dispatch({"action": "run", "job_id": project["id"], "mode": "manual"})
        self.assertEqual(first["stages"]["probe"]["status"], "ready")
        self.assertEqual(first["stages"]["extract"]["status"], "pending")
        transcript = self.engine.dispatch({"action": "run", "job_id": project["id"], "mode": "auto", "stop_after": "transcribe"})
        self.engine.dispatch({"action": "edit_source_segment", "job_id": project["id"], "segment_id": "0", "text": "Edited", "start": 0, "end": .4})
        self.engine.dispatch({"action": "update_project", "job_id": project["id"], "settings": {"whisper_model": "medium"}})
        with self.assertRaisesRegex(ValueError, "thủ công"):
            self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertEqual(self.engine.jobs.load(project["id"])["segments"][0]["text"], "Edited")

    def test_import_audio_changes_asr_key_and_imported_translation_is_checked(self):
        project = self.create()
        transcript = self.engine.dispatch({"action": "run", "job_id": project["id"], "stop_after": "transcribe"})
        first_key = transcript["stages"]["transcribe"]["key"]
        audio = self.root / "import.wav"
        tone(audio, 2)
        imported = self.engine.dispatch({"action": "import_artifact", "job_id": project["id"], "stage": "extract", "path": str(audio)})
        self.assertEqual(imported["stages"]["transcribe"]["status"], "stale")
        second = self.engine.dispatch({"action": "run_stage", "job_id": project["id"], "stage": "transcribe"})
        self.assertNotEqual(first_key, second["stages"]["transcribe"]["key"])
        bad = self.root / "translation.json"
        write_json(bad, [{"id": "missing", "translation": "No"}])
        with self.assertRaises(ValueError):
            self.engine.dispatch({"action": "import_artifact", "job_id": project["id"], "stage": "translate", "path": str(bad)})

    def test_failed_late_batch_survives_reload_and_translation_edits_have_revisions(self):
        project = self.create()
        self.engine.dispatch({"action": "run", "job_id": project["id"], "stop_after": "transcribe"})
        def fail_after_batch(translator, segments, options, emit, **kwargs):
            kwargs["on_batch"]({"0": "Saved"})
            raise RuntimeError("late failure")
        with patch("dubbing.providers.OllamaTranslator.translate", fail_after_batch):
            with self.assertRaisesRegex(RuntimeError, "late failure"):
                self.engine.dispatch({"action": "run", "job_id": project["id"]})
        saved = self.engine.jobs.load(project["id"])
        self.assertEqual(saved["segments"][0]["translation"], "Saved")
        first_translation = saved["artifacts"]["translation"]
        self.engine.dispatch({"action": "edit_segment", "job_id": project["id"], "segment_id": "0", "text": "Manual"})
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertEqual(done["segments"][0]["translation"], "Manual")
        self.assertNotEqual(first_translation, done["artifacts"]["translation"])
        self.assertEqual(read_json(Path(first_translation))["segments"][0]["translation"], "Saved")

    def test_regenerating_one_voice_cannot_mark_other_changed_voices_current(self):
        project = self.create()
        self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.engine.dispatch({"action": "update_project", "job_id": project["id"], "options": {"voice": "new"}})
        take = self.engine.dispatch({"action": "regenerate_segment", "job_id": project["id"], "segment_id": "0"})
        self.assertEqual(take["stages"]["synthesize"]["status"], "stale")
        with self.assertRaises(ValueError):
            self.engine.dispatch({"action": "run_stage", "job_id": project["id"], "stage": "align"})
        done = self.engine.dispatch({"action": "export", "job_id": project["id"]})
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.calls["tts"], 4)

    def test_moved_project_registers_and_relink_proves_source_content(self):
        project = self.create(copy_source=True)
        folder = Path(project["directory"])
        moved = self.root / "moved"
        folder.rename(moved)
        opened = self.engine.dispatch({"action": "open_project", "path": str(moved)})
        self.assertEqual(opened["directory"], str(moved))
        self.assertTrue(Path(opened["source"]).is_file())
        replacement = self.root / "renamed.mp4"
        replacement.write_bytes(self.source.read_bytes())
        relinked = self.engine.dispatch({"action": "relink_source", "job_id": project["id"], "source": str(replacement)})
        self.assertEqual(relinked["source"], str(replacement))
        bad = self.root / "different.mp4"
        bad.write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "khớp"):
            self.engine.dispatch({"action": "relink_source", "job_id": project["id"], "source": str(bad)})

    def test_manifest_cannot_import_an_artifact_outside_project(self):
        project = self.create()
        folder = Path(project["directory"])
        manifest = read_json(folder / "project.json")
        manifest["artifacts"] = {"source_audio": "../outside.wav"}
        write_json(folder / "project.json", manifest)
        with self.assertRaisesRegex(ValueError, "ngoài"):
            self.engine.jobs.load(project["id"])

    def test_edit_after_model_digest_change_cannot_revalidate_old_automatic_cues(self):
        identity = {"provider": "ollama", "model": "fixture", "digest": "old", "prompt_version": "v1"}
        with patch("dubbing.providers.translation_identity", return_value=identity):
            project = self.create()
            self.engine.dispatch({"action": "run", "job_id": project["id"]})
        with patch("dubbing.providers.translation_identity", return_value=dict(identity, digest="new")):
            changed = self.engine.dispatch({"action": "edit_segment", "job_id": project["id"], "segment_id": "0", "text": "Reviewed"})
            self.assertEqual(changed["stages"]["translate"]["status"], "stale")
            self.assertTrue(changed["segments"][1]["translation_stale"])
            with self.assertRaises(ValueError):
                self.engine.dispatch({"action": "export", "job_id": project["id"]})
            done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
            self.assertEqual(done["segments"][0]["translation"], "Reviewed")

    def test_clean_never_deletes_part_named_source_outputs_or_history(self):
        source = self.root / "recording.part.final.mp4"
        source.write_bytes(b"source")
        project = self.engine.dispatch({"action": "create_project", "source": str(source), "options": {}, "copy_source": True})
        done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertTrue(Path(done["source"]).is_file())
        self.assertTrue(Path(done["output"]).is_file())
        self.engine.dispatch({"action": "edit_segment", "job_id": project["id"], "segment_id": "0", "text": "Edit"})
        self.assertTrue(Path(done["output"]).is_file())

    def test_forgotten_legacy_id_does_not_reappear_in_list(self):
        identity = "b" * 32
        folder = self.root / "jobs" / identity
        folder.mkdir(parents=True)
        write_json(folder / "job.json", {"id": identity, "source": str(self.source), "status": "edited", "updated": 1, "segments": []})
        write_json(self.root / "project-index.json", {"version": 1, "projects": {}, "forgotten": [identity]})
        self.assertEqual(self.engine.jobs.list(), [])

    def test_cannot_create_a_project_nested_inside_another_project(self):
        project = self.create()
        self.engine.jobs.projects_dir = Path(project["directory"]) / "children"
        with self.assertRaisesRegex(ValueError, "bên trong"):
            self.create(new_project=True)

    def test_interrupted_legacy_migration_recovers_checkpoint_without_rewriting_original(self):
        identity = "c" * 32
        folder = self.root / "jobs" / identity
        folder.mkdir(parents=True)
        data = {"id": identity, "source": str(self.source), "fingerprint": fingerprint(self.source),
                "options": {}, "status": "running", "stage": "translate", "updated": 1,
                "segments": [], "warnings": [], "output": None}
        write_json(folder / "job.json", data)
        rows = [{"id": "1", "text": "Checkpoint", "start": 0, "end": .4}]
        write_json(folder / "transcript.json", {"key": "old-asr", "segments": rows})
        write_json(folder / "translated.json", {"key": "old-translation", "segments": [dict(rows[0], translation="Đã lưu")]})
        before = (folder / "job.json").read_bytes()
        migrated = self.engine.dispatch({"action": "migrate_project", "job_id": identity})
        self.assertEqual(migrated["status"], "paused")
        self.assertEqual(before, (folder / "job.json").read_bytes())
        with patch("dubbing.providers.transcribe", side_effect=AssertionError("ASR must reuse checkpoint")):
            done = self.engine.dispatch({"action": "run", "job_id": identity})
        self.assertEqual(done["segments"][0]["translation"], "Đã lưu")
        self.assertEqual(self.calls["translate"], 0)

    def test_migrated_translation_can_be_edited_without_loading_media_or_models(self):
        identity = "d" * 32
        folder = self.root / "jobs" / identity
        folder.mkdir(parents=True)
        write_json(folder / "job.json", {"id": identity, "source": str(self.source), "fingerprint": fingerprint(self.source),
            "options": {}, "status": "edited", "updated": 1, "warnings": [], "output": None,
            "segments": [{"id": "1", "text": "Saved", "translation": "Có sẵn", "start": 0, "end": .4}]})
        self.engine.dispatch({"action": "migrate_project", "job_id": identity})
        with patch.object(self.engine.media, "probe", side_effect=AssertionError("Editing must not load media")):
            edited = self.engine.dispatch({"action": "edit_segment", "job_id": identity, "segment_id": "1", "text": "Sửa"})
        self.assertEqual(edited["segments"][0]["translation"], "Sửa")

    def test_imported_transcript_runs_translation_without_forcing_asr_or_extraction(self):
        project = self.create()
        self.engine.dispatch({"action": "run_stage", "job_id": project["id"], "stage": "probe"})
        transcript = self.root / "source.srt"
        transcript.write_text("1\n00:00:00,000 --> 00:00:00,400\nOriginal\n", encoding="utf-8")
        self.engine.dispatch({"action": "import_artifact", "job_id": project["id"], "stage": "transcribe", "path": str(transcript)})
        with patch("dubbing.providers.transcribe", side_effect=AssertionError("Imported transcript must be used")), patch.object(self.engine.media, "extract", side_effect=AssertionError("Extraction not needed for text import")):
            translated = self.engine.dispatch({"action": "run_stage", "job_id": project["id"], "stage": "translate"})
            self.assertEqual(translated["stages"]["translate"]["status"], "ready")
            done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.assertEqual(done["status"], "completed")

    def test_saved_translation_receipt_allows_offline_export_with_project_model_settings(self):
        self.engine.settings["ollama_model"] = "qwen2.5:7b"
        known = {"provider": "ollama", "model": "qwen2.5:7b", "digest": "saved-weights",
                 "prompt_version": "v1", "details": {"family": "qwen2"}}
        with patch("dubbing.providers.translation_identity", return_value=known):
            project = self.create()
            done = self.engine.dispatch({"action": "run", "job_id": project["id"]})
        self.engine.settings["ollama_model"] = "qwen2.5:3b"
        def unavailable(settings):
            self.assertEqual(settings["ollama_model"], "qwen2.5:7b")
            return {"provider": "ollama", "model": settings["ollama_model"], "digest": None, "prompt_version": "v1"}
        with patch("dubbing.providers.translation_identity", side_effect=unavailable), patch("dubbing.providers.OllamaTranslator.translate", side_effect=AssertionError("Cached export must not translate")):
            exported = self.engine.dispatch({"action": "export", "job_id": project["id"]})
        self.assertEqual(exported["status"], "completed")
        self.assertEqual(exported["translation_key"], done["translation_key"])
        self.assertEqual(exported["translation_identity"], known)

    def test_offline_translation_receipt_does_not_hide_prompt_version_change(self):
        known = {"provider": "ollama", "model": "fixture", "digest": "weights", "prompt_version": "v1"}
        with patch("dubbing.providers.translation_identity", return_value=known):
            project = self.create()
            self.engine.dispatch({"action": "run", "job_id": project["id"]})
        changed_prompt = dict(known, digest=None, prompt_version="v2")
        with patch("dubbing.providers.translation_identity", return_value=changed_prompt):
            with self.assertRaises(ValueError):
                self.engine.dispatch({"action": "export", "job_id": project["id"]})


if __name__ == "__main__":
    unittest.main()
