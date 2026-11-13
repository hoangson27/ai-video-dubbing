"""Opt-in end-to-end tests with user-provided clips and installed local AI models."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from dubbing.media import Media


class LiveDubbingTests(unittest.TestCase):
    def run_clip(self, language, variable):
        if not os.environ.get(variable) or not os.environ.get("DUBBING_TEST_DATA"):
            self.skipTest(f"Set {variable} and DUBBING_TEST_DATA to run live inference.")
        source = Path(os.environ[variable]).resolve(strict=True)
        settings = {
            "hardware_mode": os.environ.get("DUBBING_TEST_MODE", "auto"),
            "whisper_model": os.environ.get("DUBBING_TEST_WHISPER", "small"),
            "ollama_model": os.environ.get("DUBBING_TEST_OLLAMA", "qwen2.5:7b"),
            "ffmpeg_path": os.environ.get("DUBBING_TEST_FFMPEG", ""),
            "ffprobe_path": os.environ.get("DUBBING_TEST_FFPROBE", ""),
        }
        data = Path(os.environ["DUBBING_TEST_DATA"]).resolve(strict=True)
        request = {
            "action": "run", "data_dir": str(data), "source": str(source), "settings": settings,
            "options": {"source_language": language, "voice": os.environ.get("DUBBING_TEST_VOICE", "")},
            "verify_packets": True,
        }
        with source.open("rb") as file:
            original = hashlib.file_digest(file, "sha256").hexdigest()
        process = subprocess.run([sys.executable, "-B", "-u", "-m", "dubbing"], input=json.dumps(request),
                                 encoding="utf-8", capture_output=True, timeout=3600)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        events = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(events[-1]["type"], "result")
        job = events[-1]["data"]
        self.assertEqual(job["status"], "completed")
        self.assertTrue(job["segments"])
        self.assertTrue(all(segment["translation"] and Path(segment["audio_path"]).is_file()
                            for segment in job["segments"]))
        with source.open("rb") as file:
            self.assertEqual(hashlib.file_digest(file, "sha256").hexdigest(), original)
        media = Media(settings)
        output = Path(job["output"])
        self.assertTrue(output.is_file())
        self.assertEqual(media.packet_digest(source), media.packet_digest(output))
        print(f"\n{language}: {output}")

    def test_mandarin_to_vietnamese(self):
        self.run_clip("zh", "DUBBING_TEST_ZH")

    def test_english_to_vietnamese(self):
        self.run_clip("en", "DUBBING_TEST_EN")
