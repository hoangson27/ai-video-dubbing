import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubbing import providers


SEGMENTS = [
    {"id": "1", "start": 0.0, "end": 1.0, "text": "你好，丽娜。"},
    {"id": "2", "start": 1.2, "end": 2.4, "text": "Where are you going?"},
]
MODEL = {"model": "qwen2.5:3b", "name": "qwen2.5:3b", "size": 1900000000,
         "digest": "local", "details": {"format": "gguf", "family": "qwen2"}}


def chat(translations):
    return {"model": MODEL["model"], "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(translations)}}


class TranslationTests(unittest.TestCase):
    def test_resume_preserves_valid_existing_ids_and_retries_only_bad_values(self):
        saved = []
        requested = []

        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint == "/api/chat":
                requested.append(payload["format"]["required"])
                prompt = json.loads(payload["messages"][1]["content"])
                self.assertEqual(prompt["previous_translations"], {"1": "Bản sửa tay"})
                self.assertEqual(payload["options"]["temperature"], 0)
                self.assertEqual(payload["format"]["properties"]["2"]["minLength"], 1)
                return chat({"2": "Bạn đi đâu?"})
            return {}

        with patch.object(providers, "ollama_request", side_effect=request):
            result = providers.OllamaTranslator({}).translate(
                SEGMENTS, {}, lambda *_: None,
                existing={"1": "Bản sửa tay", "2": " ", "unknown": "ignored"}, on_batch=saved.append,
            )
        self.assertEqual(requested, [["2"]])
        self.assertEqual(saved, [{"2": "Bạn đi đâu?"}])
        self.assertEqual(result, {"1": "Bản sửa tay", "2": "Bạn đi đâu?"})

    def test_complete_resume_needs_no_ollama(self):
        with patch.object(providers, "ollama_request", side_effect=AssertionError("should use saved IDs")):
            result = providers.OllamaTranslator({}).translate(
                SEGMENTS, {}, lambda *_: None, existing={"1": "Một", "2": "Hai"},
            )
        self.assertEqual(result, {"1": "Một", "2": "Hai"})

    def test_partial_callback_precedes_retry_and_survives_later_failure(self):
        saved = {}
        requests = []
        logs = []

        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint != "/api/chat":
                return {}
            requests.append(payload["format"]["required"])
            if len(requests) == 1:
                response = chat({"2": " ", "1": "Chào Lina."})
                response.update(prompt_eval_count=123, eval_count=17, total_duration=9000000)
                return response
            self.assertEqual(saved, {"1": "Chào Lina."})
            self.assertEqual(payload["format"]["required"], ["2"])
            return chat({"2": ""})

        with patch.object(providers, "ollama_request", side_effect=request), patch.object(providers.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "2"):
                providers.OllamaTranslator({}).translate(
                    SEGMENTS, {}, lambda level, message: logs.append(message), on_batch=saved.update,
                )
        self.assertEqual(saved, {"1": "Chào Lina."})
        self.assertLessEqual(len(requests), 5)
        self.assertTrue(any("1/2" in message for message in logs))
        self.assertTrue(any("123" in message and "17" in message for message in logs))
        self.assertFalse(any(SEGMENTS[0]["text"] in message for message in logs))

    def test_callback_pause_propagates_without_another_chat(self):
        class Paused(RuntimeError):
            pass

        saved = {}
        chats = []

        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint == "/api/chat":
                chats.append(payload)
                return chat({"1": "Chào Lina.", "2": ""})
            return {}

        def checkpoint(values):
            saved.update(values)
            raise Paused("pause after saving")

        with patch.object(providers, "ollama_request", side_effect=request):
            with self.assertRaises(Paused):
                providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda *_: None, on_batch=checkpoint)
        self.assertEqual(saved, {"1": "Chào Lina."})
        self.assertEqual(len(chats), 1)

    def test_repeated_invalid_group_is_split_without_changing_id_mapping(self):
        segments = [{"id": str(i), "start": i, "end": i + 1, "text": f"Line {i}"} for i in range(1, 5)]
        requests = []
        saved = []

        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint != "/api/chat":
                return {}
            ids = payload["format"]["required"]
            requests.append(ids)
            if len(ids) == 4:
                return chat({key: "" for key in ids})
            return chat({key: f"Câu {key}" for key in reversed(ids)})

        with patch.object(providers, "ollama_request", side_effect=request), patch.object(providers.time, "sleep"):
            result = providers.OllamaTranslator({}).translate(segments, {}, lambda *_: None, on_batch=saved.append)
        self.assertEqual(requests, [["1", "2", "3", "4"], ["1", "2", "3", "4"], ["1", "2"], ["3", "4"]])
        self.assertEqual(result, {"1": "Câu 1", "2": "Câu 2", "3": "Câu 3", "4": "Câu 4"})
        self.assertEqual(saved, [{"1": "Câu 1", "2": "Câu 2"}, {"3": "Câu 3", "4": "Câu 4"}])

    def test_duplicate_or_extra_ids_cannot_commit_a_partially_valid_response(self):
        for content in ('{"1":"Good","1":"Duplicate","2":"Good"}', '{"1":"Good","2":"Good","99":"Bad"}'):
            with self.subTest(content=content):
                saved = []
                response = chat({})
                response["message"]["content"] = content
                replies = iter([response, chat({"2": "Hai", "1": "Một"})])

                def request(endpoint, payload=None, **_):
                    if endpoint == "/api/tags":
                        return {"models": [MODEL]}
                    if endpoint == "/api/chat":
                        self.assertEqual(saved, [])
                        return next(replies)
                    return {}

                with patch.object(providers, "ollama_request", side_effect=request), patch.object(providers.time, "sleep"):
                    result = providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda *_: None, on_batch=saved.append)
                self.assertEqual(result, {"1": "Một", "2": "Hai"})
                self.assertEqual(saved, [{"1": "Một", "2": "Hai"}])

    def test_timing_logs_do_not_expose_provider_error_body(self):
        logs = []
        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint == "/api/chat":
                raise RuntimeError("HTTP failure: " + SEGMENTS[0]["text"])
            return {}
        with patch.object(providers, "ollama_request", side_effect=request), patch.object(providers.time, "sleep"):
            with self.assertRaises(RuntimeError) as failure:
                providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda level, message: logs.append(message))
        self.assertNotIn(SEGMENTS[0]["text"], str(failure.exception))
        self.assertFalse(any(SEGMENTS[0]["text"] in message for message in logs))

    def test_identity_includes_local_digest_and_prompt_and_retains_explicit_7b(self):
        model = dict(MODEL, model="qwen2.5:7b", name="qwen2.5:7b", digest="sha256:seven")
        with patch.object(providers, "ollama_models", return_value=[model]):
            identity = providers.translation_identity({"ollama_model": "qwen2.5:7b"})
        self.assertEqual(identity["provider"], "ollama")
        self.assertEqual(identity["model"], "qwen2.5:7b")
        self.assertEqual(identity["digest"], "sha256:seven")
        self.assertTrue(identity["prompt_version"])

    def test_identity_is_stable_without_installed_ollama(self):
        with patch.object(providers, "ollama_models", side_effect=providers.OllamaUnavailable("missing")):
            identity = providers.translation_identity({})
        self.assertEqual(identity["model"], "qwen2.5:3b")
        self.assertIsNone(identity["digest"])

    def test_stage_device_overrides_old_global_mode_for_ollama(self):
        options = []
        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint == "/api/chat":
                options.append(payload["options"])
                return chat({"1": "Một", "2": "Hai"})
            return {}
        with patch.object(providers, "ollama_request", side_effect=request):
            providers.OllamaTranslator({"hardware_mode": "gpu", "translation_device": "cpu"}).translate(
                SEGMENTS, {}, lambda *_: None,
            )
        self.assertEqual(options[0]["num_gpu"], 0)

    def test_retries_invalid_ids_and_preserves_association(self):
        translations = {"2": "Bạn đi đâu?", "1": "Chào Lina."}
        responses = [{"models": [MODEL]}, chat({"99": "Chào"}), chat(translations), {}]
        with patch.object(providers, "ollama_request", side_effect=responses), patch.object(providers.time, "sleep"):
            result = providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda *_: None)
        self.assertEqual(result, translations)

    def test_single_segment_has_adjacent_context_and_only_its_id(self):
        def request(endpoint, payload=None, **_):
            if endpoint == "/api/tags":
                return {"models": [MODEL]}
            if endpoint == "/api/chat":
                self.assertEqual(payload["format"]["required"], ["2"])
                self.assertIn(SEGMENTS[0]["text"], payload["messages"][1]["content"])
                return chat({"2": "Cậu định đi đâu?"})
            return {}

        with patch.object(providers, "ollama_request", side_effect=request):
            result = providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda *_: None, target_id="2")
        self.assertEqual(result, {"2": "Cậu định đi đâu?"})

    def test_rejects_cloud_model_before_submitting_transcript(self):
        remote = dict(MODEL, remote_model="remote-qwen", remote_host="https://ollama.com")
        with patch.object(providers, "ollama_request", return_value={"models": [remote]}) as request:
            with self.assertRaisesRegex(RuntimeError, "local"):
                providers.OllamaTranslator({}).translate(SEGMENTS, {}, lambda *_: None)
        request.assert_called_once()


class WhisperTests(unittest.TestCase):
    def test_asr_device_overrides_old_global_gpu(self):
        self.assertEqual(self._transcribe("gpu", RuntimeError("must not use GPU"), asr_device="cpu"), [
            {"id": "1", "start": 1.2, "end": 3.4, "text": "你好"},
        ])

    def test_auto_falls_back_when_lazy_transcription_cannot_load_cuda(self):
        self.assertEqual(self._transcribe("auto", RuntimeError("Library cublas64_12.dll is not found")), [
            {"id": "1", "start": 1.2, "end": 3.4, "text": "你好"},
        ])

    def test_auto_surfaces_out_of_memory(self):
        with self.assertRaisesRegex(RuntimeError, "out of memory"):
            self._transcribe("auto", RuntimeError("CUDA failed with error out of memory"))

    def test_forced_gpu_does_not_fall_back(self):
        with self.assertRaisesRegex(RuntimeError, "cublas64_12.dll"):
            self._transcribe("gpu", RuntimeError("Library cublas64_12.dll is not found"))

    def _transcribe(self, mode, error, **devices):
        class Whisper:
            def __init__(self, model_path, device, compute_type, local_files_only):
                self.device = device
                assert local_files_only and Path(model_path).name == "small"

            def transcribe(self, wav, language, vad_filter):
                def run():
                    if self.device == "cuda":
                        raise error
                    yield SimpleNamespace(start=1.2, end=3.4, text=" 你好 ")
                return run(), SimpleNamespace(language="zh", language_probability=0.98)

        with tempfile.TemporaryDirectory() as temp, patch.object(providers, "whisper_cuda", return_value=(1, "")), \
             patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=Whisper)}), patch.dict(providers.os.environ):
            data_dir = Path(temp)
            weights = data_dir / "models" / "whisper" / "small"
            weights.mkdir(parents=True)
            for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (weights / name).write_bytes(b"fixture")
            return providers.transcribe(data_dir / "source.wav", {"hardware_mode": mode, **devices}, "auto", data_dir, lambda *_: None)


class VieNeuTests(unittest.TestCase):
    def test_tts_device_overrides_global_mode_and_model_stays_lazy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(providers.VieNeuSpeech, "_load", side_effect=AssertionError("lazy")):
            speech = providers.VieNeuSpeech({"hardware_mode": "gpu", "tts_device": "cpu"}, Path(temp), lambda *_: None)
        self.assertEqual(speech.mode, "cpu")
        self.assertIsNone(speech.tts)

    def test_first_available_voice_matches_model_default(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sdk = directory / "sdk"
            sdk.mkdir()
            presets = sdk / "voices_v3_turbo.json"
            presets.write_text(json.dumps({
                "default_voice": "Hải Đăng",
                "presets": {
                    "Trúc Ly": {"speaker_emb": [0.1] * 192},
                    "Hải Đăng": {"speaker_emb": [0.2] * 192},
                },
            }), encoding="utf-8")
            distribution = SimpleNamespace(locate_file=lambda _: presets)
            with patch.object(providers.metadata, "distribution", return_value=distribution):
                voices = providers.list_tts_voices(directory)
        self.assertEqual([item["id"] for item in voices], ["Hải Đăng", "Trúc Ly"])

    def test_rejects_modified_openmoss_custom_code(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            model = data_dir / "models" / "vieneu"
            codec = data_dir / "models" / "vieneu-codec-gpu"
            (model / "update").mkdir(parents=True)
            codec.mkdir(parents=True)
            for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
                (model / "update" / name).write_text("{}", encoding="utf-8")
            (model / "speaker_encoder.onnx").write_bytes(b"fixture")
            for name in (
                "config.json", "configuration_moss_audio_tokenizer.py", "modeling_moss_audio_tokenizer.py",
                "model-00001-of-00001.safetensors", "model.safetensors.index.json",
            ):
                (codec / name).write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "OpenMOSS"):
                providers.tts_gpu_weights(model, data_dir)


if __name__ == "__main__":
    unittest.main()
