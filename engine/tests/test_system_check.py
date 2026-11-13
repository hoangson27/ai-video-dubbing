import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from dubbing import providers, system_check


class SystemCheckTests(unittest.TestCase):
    def _device_check(self, settings):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            data = Path(temp)
            for target, name, value in (
                (system_check, "_package", ("installed", "fixture")),
                (system_check, "_inventory", {"cuda_devices": 0}),
                (system_check, "_command", "ffmpeg version fixture"),
                (system_check, "resolve_tool", "fixture"),
                (system_check, "whisper_cuda", (0, "CUDA unavailable")),
                (providers, "whisper_weights", data),
                (providers, "tts_gpu_weights", data),
                (providers, "tts_cpu_weights", None),
                (providers, "tts_cuda", "TTS CUDA unavailable"),
                (providers, "list_tts_voices", [{"id": "voice", "label": "Voice"}]),
                (providers, "ollama_models", [{"name": "qwen2.5:3b", "size": 10}]),
            ):
                patches.enter_context(patch.object(target, name, return_value=value))
            return {item["name"]: item for item in system_check.check(settings, data)["checks"]}

    def test_independent_cpu_devices_override_global_gpu_diagnostics(self):
        checks = self._device_check({
            "hardware_mode": "gpu", "asr_device": "cpu", "translation_device": "cpu", "tts_device": "cpu",
        })
        self.assertEqual(checks["Whisper CUDA"]["status"], "installed")
        self.assertNotIn("VieNeu-TTS CUDA", checks)
        self.assertIn("ONNX/CPU", checks["VieNeu-TTS model"]["detail"])
        self.assertIn("CPU", checks["Qwen"]["detail"])

    def test_independent_gpu_devices_override_global_cpu_diagnostics(self):
        checks = self._device_check({
            "hardware_mode": "cpu", "asr_device": "gpu", "translation_device": "gpu", "tts_device": "gpu",
        })
        self.assertEqual(checks["Whisper CUDA"]["status"], "incompatible")
        self.assertEqual(checks["VieNeu-TTS CUDA"]["status"], "incompatible")
        self.assertIn("GPU", checks["Qwen"]["detail"])

    def test_invalid_stage_device_is_reported_instead_of_crashing(self):
        checks = self._device_check({"asr_device": "banana"})
        self.assertTrue(any(item["status"] == "incompatible" and "ASR" in item["name"] for item in checks.values()))

    def test_unavailable_ollama_is_missing(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            providers, "ollama_request", side_effect=providers.OllamaUnavailable("Ollama không phản hồi"),
        ):
            result = system_check.check({}, Path(temp))
        self.assertFalse(result["ready"])
        self.assertEqual({item["name"]: item["status"] for item in result["checks"]}["Ollama"], "missing")

    def test_missing_qwen_has_separate_status(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(providers, "ollama_request", return_value={"models": []}):
            result = system_check.check({}, Path(temp))
        status = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(status["Ollama"], "installed")
        self.assertEqual(status["Qwen"], "missing")

    def test_forced_gpu_without_cuda_is_incompatible(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(providers, "ollama_request", return_value={"models": []}), \
             patch.object(system_check, "whisper_cuda", return_value=(0, "Không tìm thấy CUDA")):
            result = system_check.check({"hardware_mode": "gpu"}, Path(temp))
        status = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(result["hardware"]["cuda_devices"], 0)
        self.assertEqual(status["Whisper CUDA"], "incompatible")

    def test_whisper_model_without_vocabulary_is_missing(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(providers, "ollama_request", return_value={"models": []}):
            data = Path(temp)
            weights = data / "models" / "whisper" / "small"
            weights.mkdir(parents=True)
            for name in ("config.json", "model.bin", "tokenizer.json"):
                (weights / name).write_bytes(b"fixture")
            result = system_check.check({}, data)
        status = {item["name"]: item["status"] for item in result["checks"]}
        self.assertEqual(status["Whisper model"], "missing")


if __name__ == "__main__":
    unittest.main()
