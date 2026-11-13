import errno
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dubbing.jobs import write_json


def windows_denial(code):
    error = PermissionError(errno.EACCES, "File is held by another reader")
    error.winerror = code
    return error


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "dự án.json"
        self.original = {"revision": 1, "text": "Bản trước"}
        self.original_bytes = json.dumps(self.original, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(self.original_bytes)

    @unittest.skipUnless(os.name == "nt", "Windows sharing violation retry")
    def test_windows_temporary_replace_denials_retry_without_touching_original(self):
        replace = Path.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                self.path.write_bytes(self.original_bytes)
                candidate_paths = []
                payload = {"revision": 2, "text": "Bản mới"}

                def occasionally_locked(candidate, target):
                    self.assertEqual(Path(target), self.path)
                    self.assertEqual(self.path.read_bytes(), self.original_bytes)
                    self.assertEqual(json.loads(candidate.read_text(encoding="utf-8")), payload)
                    candidate_paths.append(candidate)
                    if len(candidate_paths) < 3:
                        raise windows_denial(code)
                    return replace(candidate, target)

                with patch.object(Path, "replace", occasionally_locked), patch("dubbing.jobs.time.sleep") as sleep:
                    write_json(self.path, payload)
                self.assertEqual(len(candidate_paths), 3)
                self.assertEqual(len(set(candidate_paths)), 1)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), payload)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows persistent file-lock behavior")
    def test_persistent_lock_keeps_valid_unique_candidates_and_original(self):
        payloads = [{"revision": 2}, {"revision": 3}]
        for payload in payloads:
            denied = windows_denial(5)
            with patch.object(Path, "replace", side_effect=denied), patch("dubbing.jobs.time.sleep") as sleep:
                with self.assertRaises(PermissionError) as raised:
                    write_json(self.path, payload)
            self.assertIs(raised.exception.__cause__, denied)
            self.assertIn(str(self.path), str(raised.exception))
            self.assertIn(".pending.json", str(raised.exception))
            self.assertIn("giữ", str(raised.exception))
            self.assertGreater(sleep.call_count, 0)
            self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 3)
            self.assertEqual(self.path.read_bytes(), self.original_bytes)
        candidates = list(self.root.glob("*.pending.json"))
        self.assertEqual(len(candidates), 2)
        self.assertEqual(sorted(json.loads(path.read_text(encoding="utf-8"))["revision"] for path in candidates), [2, 3])

    def test_unrelated_replace_error_is_not_retried_or_wrapped(self):
        disk_error = OSError(errno.ENOSPC, "Disk full")
        with patch.object(Path, "replace", side_effect=disk_error) as replace, patch("dubbing.jobs.time.sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                write_json(self.path, {"revision": 2})
        self.assertIs(raised.exception, disk_error)
        replace.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)

    def test_invalid_json_does_not_create_candidate_or_change_original(self):
        for value in (float("nan"), object()):
            with self.subTest(value=type(value).__name__), self.assertRaises((ValueError, TypeError)):
                write_json(self.path, {"value": value})
            self.assertEqual(self.path.read_bytes(), self.original_bytes)
            self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_failed_write_cleanup_cannot_mask_original_disk_error(self):
        disk_error = OSError(errno.ENOSPC, "Disk full while writing candidate")
        named_temporary = tempfile.NamedTemporaryFile

        @contextmanager
        def partially_written(*args, **kwargs):
            with named_temporary(*args, **kwargs) as stream:
                stream.write("{")
                yield SimpleNamespace(name=stream.name, write=Mock(side_effect=disk_error))

        with patch("tempfile.NamedTemporaryFile", partially_written), \
             patch.object(Path, "unlink", side_effect=windows_denial(5)), \
             patch("dubbing.jobs.time.sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                write_json(self.path, {"revision": 2})
        self.assertIs(raised.exception, disk_error)
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertNotIn("Đã giữ", str(raised.exception))
        sleep.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows read handle blocks atomic replacement")
    def test_real_windows_reader_lock_released_while_save_retries(self):
        reader = self.path.open("rb")
        release = threading.Timer(0.12, reader.close)
        release.start()
        try:
            payload = {"revision": 2, "text": "Đã lưu sau khi đóng handle"}
            write_json(self.path, payload)
        finally:
            release.join()
            reader.close()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), payload)
        self.assertEqual(list(self.root.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
