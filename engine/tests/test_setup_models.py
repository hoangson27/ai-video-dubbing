import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dubbing import providers, setup_models, system_check


class ModelSetupTests(unittest.TestCase):
    def whisper_fixture(self, directory, payload=b"complete model fixture", *, managed=True, receipt=True):
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("config.json", "tokenizer.json", "vocabulary.txt"):
            (directory / name).write_bytes(b"fixture")
        (directory / "model.bin").write_bytes(payload)
        if managed:
            cache = directory / ".cache" / "huggingface" / "download"
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "model.bin.lock").touch()
            if receipt:
                (cache / "model.bin.metadata").write_text(
                    "a" * 40 + "\n" + hashlib.sha256(payload).hexdigest() + "\n0\n", encoding="utf-8")
        return directory

    def run_setup(self, data, download, snapshot, *extra):
        whisper = types.ModuleType("faster_whisper")
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = download
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = snapshot
        with patch.dict(sys.modules, {
            "faster_whisper": whisper, "faster_whisper.utils": utils, "huggingface_hub": hub,
        }), patch.object(sys, "argv", ["setup_models", "--data-dir", str(data), *extra]):
            setup_models.main()

    def test_existing_models_are_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            download, snapshot = Mock(), Mock()
            with patch.object(setup_models, "whisper_weights"), patch.object(setup_models, "tts_cpu_weights"):
                self.run_setup(data, download, snapshot)
            download.assert_not_called()
            snapshot.assert_not_called()

    def test_unfinished_hf_whisper_is_rejected_instead_of_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            self.whisper_fixture(data / "models" / "whisper" / "small", receipt=False)
            with self.assertRaisesRegex(FileNotFoundError, "model.bin.metadata"):
                providers.whisper_weights({}, data)

    def test_hf_whisper_hash_detects_same_size_corruption_after_prior_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            directory = self.whisper_fixture(data / "models" / "whisper" / "small")
            self.assertEqual(providers.whisper_weights({}, data), directory)
            (directory / "model.bin").write_bytes(b"corrupt! model fixture")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                providers.whisper_weights({}, data)

    def test_manual_whisper_without_hf_metadata_keeps_existing_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            directory = self.whisper_fixture(data / "models" / "whisper" / "small", managed=False)
            self.assertEqual(providers.whisper_weights({}, data), directory)

    def test_setup_validates_fresh_whisper_before_swapping_and_preserves_old_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            directory = self.whisper_fixture(data / "models" / "whisper" / "small")
            (directory / "model.bin").write_bytes(b"corrupted old weights")

            def download(model, output_dir):
                self.assertEqual(model, "small")
                self.assertNotEqual(Path(output_dir), directory)
                self.assertEqual((directory / "model.bin").read_bytes(), b"corrupted old weights")
                self.whisper_fixture(Path(output_dir), b"fresh validated weights")

            with patch.object(setup_models, "tts_cpu_weights"):
                self.run_setup(data, download, Mock())
            self.assertEqual((directory / "model.bin").read_bytes(), b"fresh validated weights")
            backups = list(directory.parent.glob("small.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "model.bin").read_bytes(), b"corrupted old weights")
            self.assertEqual(providers.whisper_weights({}, data), directory)

    def test_failed_whisper_download_or_validation_leaves_original_untouched(self):
        for failure in ("download", "validation"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                data = Path(temporary)
                directory = self.whisper_fixture(data / "models" / "whisper" / "small")
                (directory / "model.bin").write_bytes(b"corrupted old weights")
                receipt = directory / ".cache" / "huggingface" / "download" / "model.bin.metadata"
                original_receipt = receipt.read_bytes()

                def download(model, output_dir):
                    if failure == "download":
                        raise RuntimeError("fixture interrupted download")
                    self.whisper_fixture(Path(output_dir), receipt=False)

                with patch.object(setup_models, "tts_cpu_weights"):
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.run_setup(data, download, Mock())
                self.assertEqual((directory / "model.bin").read_bytes(), b"corrupted old weights")
                self.assertEqual(receipt.read_bytes(), original_receipt)
                self.assertEqual(list(directory.parent.glob("small.backup-*")), [])

    def test_setup_repairs_non_utf8_hf_receipt_and_preserves_the_invalid_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            directory = self.whisper_fixture(data / "models" / "whisper" / "small")
            relative_receipt = Path(".cache/huggingface/download/model.bin.metadata")
            (directory / relative_receipt).write_bytes(b"\xffinvalid receipt")

            def download(model, output_dir):
                self.whisper_fixture(Path(output_dir), b"fresh validated weights")

            with patch.object(setup_models, "tts_cpu_weights"):
                self.run_setup(data, download, Mock())
            self.assertEqual((directory / "model.bin").read_bytes(), b"fresh validated weights")
            backup = next(directory.parent.glob("small.backup-*"))
            self.assertEqual((backup / relative_receipt).read_bytes(), b"\xffinvalid receipt")

    def test_system_check_reports_corrupt_managed_whisper_as_incompatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            directory = self.whisper_fixture(data / "models" / "whisper" / "small")
            (directory / "model.bin").write_bytes(b"corrupted old weights")
            with patch.object(system_check, "_package", return_value=("missing", "fixture")), \
                 patch.object(system_check, "_inventory", return_value={}), \
                 patch.object(system_check, "whisper_cuda", return_value=(0, "fixture")), \
                 patch.object(system_check, "resolve_tool", side_effect=FileNotFoundError("fixture")), \
                 patch.object(providers, "ollama_models", side_effect=providers.OllamaUnavailable("fixture")):
                report = system_check.check({}, data)
            row = next(item for item in report["checks"] if item["name"] == "Whisper model")
            self.assertEqual(row["status"], "incompatible")
            self.assertIn("SHA256", row["detail"])
            self.assertFalse(report["ready"])

    def test_incomplete_custom_weights_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            custom = data / "user-weights"
            custom.mkdir()
            original = custom / "model.bin"
            original.write_bytes(b"user model")
            download, snapshot = Mock(), Mock()
            with patch.object(setup_models, "whisper_weights"), patch.object(setup_models, "tts_cpu_weights", side_effect=FileNotFoundError):
                with self.assertRaisesRegex(ValueError, "existing custom weights are preserved"):
                    self.run_setup(data, download, snapshot, "--tts-model-path", str(custom))
            self.assertEqual(original.read_bytes(), b"user model")
            snapshot.assert_not_called()

    def test_downloads_use_pinned_revisions_and_custom_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            custom = data / "chosen-weights"
            codec = custom / "hub" / "models--codec" / "snapshots" / setup_models.CODEC_ONNX_REVISION
            snapshot = Mock(return_value=str(codec))
            with patch.object(setup_models, "whisper_weights"), patch.object(setup_models, "tts_cpu_weights", side_effect=FileNotFoundError):
                self.run_setup(data, Mock(), snapshot, "--tts-model-path", str(custom))
            calls = snapshot.call_args_list
            self.assertEqual(calls[0].kwargs["revision"], setup_models.VIENEU_REVISION)
            self.assertEqual(calls[0].kwargs["local_dir"], custom)
            self.assertEqual(calls[1].kwargs["revision"], setup_models.CODEC_ONNX_REVISION)
            self.assertEqual((codec.parent.parent / "refs" / "main").read_text(), setup_models.CODEC_ONNX_REVISION)


if __name__ == "__main__":
    unittest.main()
