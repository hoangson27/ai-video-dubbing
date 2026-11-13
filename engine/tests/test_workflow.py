import shutil
import subprocess
import tempfile
import unittest
import wave
import json
import sys
from array import array
from pathlib import Path
from unittest.mock import patch

from dubbing.alignment import RATE, align
from dubbing.jobs import Jobs
from dubbing.media import Media
from dubbing.pipeline import Engine


def tone(path, seconds):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(array("h", [500, -500] * int(RATE * seconds / 2)).tobytes())


class AlignmentTests(unittest.TestCase):
    def test_gaps_and_overlaps_are_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tone(root / "a.wav", 0.8)
            tone(root / "b.wav", 0.2)
            segments = [
                {"id": "a", "start": 0.25, "end": 0.9, "audio_path": str(root / "a.wav")},
                {"id": "b", "start": 0.7, "end": 1, "audio_path": str(root / "b.wav")},
            ]
            output = root / "dubbed.wav"
            warnings = align(segments, 1.5, output, lambda *_: None, lambda: None)
            self.assertIn("a", warnings)
            self.assertNotIn("b", warnings)
            with wave.open(str(output)) as wav:
                self.assertEqual(wav.getnframes(), round(1.5 * RATE))
                self.assertEqual(wav.readframes(RATE // 4), bytes(RATE // 2))
                self.assertNotEqual(wav.readframes(RATE // 4), bytes(RATE // 2))
                wav.setpos(RATE)
                self.assertEqual(wav.readframes(RATE // 2), bytes(RATE))

    def test_short_imported_source_audio_still_fails_duration_validation(self):
        with tempfile.TemporaryDirectory() as root:
            audio = Path(root) / "replacement.wav"
            tone(audio, 1)
            with self.assertRaisesRegex(ValueError, "Thời lượng"):
                Engine.validate_wav(audio, 3, source_audio=True)


class JobStateTests(unittest.TestCase):
    def test_explicit_recovery_keeps_checkpoints_and_loading_is_readonly(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            jobs = Jobs(root)
            job = jobs.create(source, {})
            cancel = root / "cancel" / "test"
            cancel.parent.mkdir()
            job.update(status="running", stage="mux", cancel_path=str(cancel))
            jobs.save(job)
            transcript = jobs.directory(job["id"]) / "transcript.json"
            transcript.write_text("[]", encoding="utf-8")
            cancel.touch()
            self.assertEqual(jobs.load(job["id"])["status"], "running")
            jobs.recover(job)
            self.assertEqual(job["status"], "paused")
            self.assertTrue(transcript.is_file())
            job.update(status="running", cancel_path=None)
            jobs.save(job)
            self.assertEqual(jobs.load(job["id"])["status"], "running")
            with self.assertRaises(ValueError):
                jobs.load("../../source")

    def test_process_preserves_translation_and_tts_when_mux_fails(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            counters = {"transcribe": 0, "translate": 0, "tts": 0}

            class AudioTools:
                def probe(self, path):
                    return {"duration": 1.0, "audio_tracks": [{"index": 1}]}

                def extract(self, source, metadata, destination):
                    tone(destination, 1)

                def retime(self, source, destination, speed):
                    shutil.copyfile(source, destination)

                def mux(self, source, metadata, audio, destination):
                    if failing:
                        raise RuntimeError("mux fixture failure")
                    destination.write_bytes(b"muxed")

            def transcribe(*_):
                counters["transcribe"] += 1
                return [{"id": "1", "start": 0.2, "end": 0.8, "text": "Hello"}]

            class Translator:
                def __init__(self, settings):
                    pass

                def translate(self, segments, options, emit, **kwargs):
                    counters["translate"] += 1
                    return {"1": "Xin chào"}

            class Speech:
                def __init__(self, settings, data_dir, emit):
                    pass

                def synthesize(self, text, voice, destination):
                    counters["tts"] += 1
                    tone(destination, 0.2)

            engine = Engine(root, {}, None, lambda _: None)
            engine.media = AudioTools()
            with patch.multiple("dubbing.providers", transcribe=transcribe,
                                OllamaTranslator=Translator, VieNeuSpeech=Speech,
                                translation_identity=lambda _: {"model": "fixture"}):
                failing = True
                with self.assertRaisesRegex(RuntimeError, "mux fixture failure"):
                    engine.dispatch({"action": "run", "source": str(source), "options": {"voice": "test"}})
                job_id = engine.dispatch({"action": "list_jobs"})[0]["id"]
                self.assertEqual(engine.dispatch({"action": "get_job", "job_id": job_id})["status"], "failed")
                failing = False
                done = engine.dispatch({"action": "run", "job_id": job_id})
                self.assertEqual(done["status"], "completed")
                self.assertEqual(counters, {"transcribe": 1, "translate": 1, "tts": 1})
                self.assertTrue(Path(done["output"]).is_file())
                edited = engine.dispatch({"action": "edit_segment", "job_id": job_id,
                                          "segment_id": "1", "text": "Chào bạn"})
                self.assertFalse(edited["segments"][0].get("audio_path"))
                done = engine.dispatch({"action": "export", "job_id": job_id})
                self.assertEqual(done["segments"][0]["translation"], "Chào bạn")
                self.assertEqual(counters, {"transcribe": 1, "translate": 1, "tts": 2})
                engine.dispatch({"action": "run", "source": str(source), "options": {"voice": "test"}})
                self.assertEqual(counters, {"transcribe": 1, "translate": 1, "tts": 2})
                take = engine.dispatch({"action": "regenerate_segment", "job_id": job_id, "segment_id": "1"})
                self.assertEqual(take["status"], "edited")
                self.assertIsNone(take["output"])
                new_audio = take["segments"][0]["audio_path"]
                self.assertNotEqual(new_audio, done["segments"][0]["audio_path"])
                self.assertEqual(counters["tts"], 3)
                exported = engine.dispatch({"action": "export", "job_id": job_id})
                self.assertEqual(exported["segments"][0]["audio_path"], new_audio)
                self.assertEqual(counters["tts"], 3)


class ProtocolTests(unittest.TestCase):
    def test_cli_returns_json_lines_for_success_failure_and_cancellation(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            request = {"data_dir": str(root), "action": "list_jobs"}

            def execute(request):
                result = subprocess.run([sys.executable, "-B", "-u", "-m", "dubbing"],
                                        input=json.dumps(request), capture_output=True, encoding="utf-8", timeout=10)
                return result.returncode, [json.loads(line) for line in result.stdout.splitlines()]

            self.assertEqual(execute(request), (0, [{"type": "result", "data": []}]))
            code, events = execute(dict(request, action="bad_action"))
            self.assertEqual(code, 1)
            self.assertEqual(events[0]["type"], "error")
            marker = root / "cancel" / "fixture"
            marker.parent.mkdir()
            marker.touch()
            code, events = execute(dict(request, cancel_path=str(marker)))
            self.assertEqual(code, 130)
            self.assertEqual(events[0]["type"], "error")
            self.assertFalse(marker.exists())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe absent")
class MediaTests(unittest.TestCase):
    def test_extract_pads_shorter_aac_audio_through_video_end(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "short audio.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=3",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:v", "mpeg4", "-c:a", "aac", str(source),
            ], check=True)
            original = source.read_bytes()
            engine = Engine(root, {}, None, lambda _: None)
            project = engine.dispatch({"action": "run", "source": str(source), "stop_after": "extract"})
            self.assertEqual(project["stages"]["extract"]["status"], "ready")
            with wave.open(project["artifacts"]["source_audio"]) as wav:
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getnframes(), 48000)
                self.assertNotEqual(wav.readframes(8000), bytes(16000))
                wav.setpos(32000)
                self.assertEqual(wav.readframes(16000), bytes(32000))
            self.assertEqual(source.read_bytes(), original)

    def test_probe_uses_video_span_when_other_streams_start_earlier(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "late video.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-itsoffset", "1", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-c:a", "aac", str(source),
            ], check=True)
            media = Media({})
            original = media.probe(source)
            self.assertAlmostEqual(original["duration"], 2.0, delta=0.04)
            audio = root / "dubbed.wav"
            tone(audio, 2)
            media.mux(source, original, audio, root / "output.mp4")
            self.assertAlmostEqual(media.probe(root / "output.mp4")["duration"], 2.0, delta=0.04)

    def test_prepares_h264_proxy_from_odd_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "odd.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=161x91:r=12:d=1",
                "-c:v", "ffv1", str(source),
            ], check=True)
            media = Media({})
            output = source.with_suffix(".mp4")
            media.preview(source, output)
            info = media.probe(output)
            self.assertEqual(info["codec"], "h264")
            self.assertEqual(info["width"] % 2, 0)

    def test_extract_inserts_silence_for_audio_start_delay(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "offset.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=2",
                "-itsoffset", "0.55", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:v", "mpeg4", "-c:a", "aac", str(source),
            ], check=True)
            media = Media({})
            audio = source.with_suffix(".wav")
            media.extract(source, media.probe(source), audio)
            with wave.open(str(audio)) as wav:
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.readframes(7000), bytes(14000))
                self.assertNotEqual(wav.readframes(7000), bytes(14000))

    def test_exports_incompatible_video_codec_to_mkv(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=1",
                "-c:v", "huffyuv", str(source),
            ], check=True)
            audio = root / "dubbed.wav"
            tone(audio, 1)
            engine = Engine(root, {}, None, lambda _: None)
            job = engine.jobs.create(source, {})
            output = engine.publish(job, source, engine.media.probe(source), audio, True)
            self.assertEqual(output.suffix, ".mkv")
            self.assertEqual(engine.media.packet_digest(source), engine.media.packet_digest(output))
    def test_mux_preserves_video_packets_and_never_overwrites_source(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source video.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "mpeg4", "-c:a", "aac", str(source),
            ], check=True)
            original = source.read_bytes()
            tone(root / "dubbed.wav", 2)
            media = Media({})
            source_info = media.probe(source)
            output = root / "new video.mp4"
            media.mux(source, source_info, root / "dubbed.wav", output)
            self.assertEqual(media.packet_digest(source), media.packet_digest(output))
            self.assertEqual(media.probe(output)["duration"], 2)
            with self.assertRaises(ValueError):
                media.mux(source, source_info, root / "dubbed.wav", source)
            self.assertEqual(source.read_bytes(), original)

    def test_probe_rejects_corrupt_input_and_detects_silent_video(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "silent.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=1",
                "-c:v", "mpeg4", str(source),
            ], check=True)
            media = Media({})
            self.assertEqual(media.probe(source)["audio_tracks"], [])
            invalid = Path(root) / "broken.mp4"
            invalid.write_bytes(b"bad input")
            with self.assertRaises(RuntimeError):
                media.probe(invalid)
            with self.assertRaises(ValueError):
                media.probe(invalid.with_suffix(".wmv"))


if __name__ == "__main__":
    unittest.main()
