import importlib.util
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import patch


class RelocateVenvTests(unittest.TestCase):
    def helper(self):
        path = Path(__file__).resolve().parents[2] / "scripts" / "relocate-venv.py"
        self.assertTrue(path.is_file(), "copied venv needs a metadata-only relocation helper")
        spec = importlib.util.spec_from_file_location("relocate_venv", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, root):
        base = root / "runtime" / "python311-nuget"
        venv = root / "venv"
        site = venv / "Lib" / "site-packages"
        base.mkdir(parents=True)
        site.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text(f"home = {base}\ninclude-system-site-packages = false\n", encoding="utf-8")
        return base, venv, site

    def test_regenerates_console_and_gui_launchers_without_importing_entrypoint_modules(self):
        helper = self.helper()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".dev"
            base, venv, site = self.fixture(root)
            info = site / "relocation_fixture-1.0.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Metadata-Version: 2.1\nName: relocation-fixture\nVersion: 1.0\n", encoding="utf-8")
            (info / "entry_points.txt").write_text(
                "[console_scripts]\nrelocation-fixture = missing_entrypoint_fixture:main\n"
                "[gui_scripts]\nrelocation-gui = missing_entrypoint_fixture:main\n", encoding="utf-8")
            executable = venv / "Scripts" / "python.exe"
            distributions = list(metadata.distributions(path=[str(site)]))
            with patch.object(helper.sys, "prefix", str(venv)), patch.object(helper.sys, "base_prefix", str(base)), \
                 patch.object(helper.sys, "executable", str(executable)), \
                 patch.object(helper.metadata, "distributions", return_value=distributions):
                generated = helper.relocate(root)
            self.assertNotIn("missing_entrypoint_fixture", sys.modules)
            scripts = venv / "Scripts"
            for name in ("relocation-fixture", "relocation-gui"):
                launcher = scripts / f"{name}.exe"
                self.assertTrue(launcher.is_file())
                interpreter = executable.with_name("pythonw.exe") if name == "relocation-gui" else executable
                self.assertTrue(str(interpreter).encode("utf-8") in launcher.read_bytes(), "launcher must reference the new interpreter")
                self.assertIn(str(launcher), generated)

    def test_refuses_external_base_system_site_packages_and_escaping_pth_before_writing(self):
        helper = self.helper()
        for fault in ("external-base", "system-site", "old-pth", "relative-pth"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / ".dev"
                base, venv, site = self.fixture(root)
                if fault == "system-site":
                    (venv / "pyvenv.cfg").write_text("include-system-site-packages = true\n", encoding="utf-8")
                elif fault == "old-pth":
                    (site / "old.pth").write_text("C:\\old\\site-packages\n", encoding="utf-8")
                elif fault == "relative-pth":
                    (site / "old.pth").write_text("../../../../external\n", encoding="utf-8")
                actual_base = Path(temp) / "old-base" if fault == "external-base" else base
                with patch.object(helper.sys, "prefix", str(venv)), patch.object(helper.sys, "base_prefix", str(actual_base)), \
                     patch.object(helper.sys, "executable", str(venv / "Scripts" / "python.exe")):
                    with self.assertRaises(ValueError):
                        helper.relocate(root)
                self.assertFalse((venv / "Scripts").exists())

    def test_rebuilt_console_launcher_runs_with_current_venv_interpreter(self):
        helper = self.helper()
        # Distlib's launcher can be checked without copying heavy packages or downloading anything.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            info = directory / "fixture-1.0.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n", encoding="utf-8")
            (info / "entry_points.txt").write_text("[console_scripts]\nrelocation-probe = json.tool:main\n", encoding="utf-8")
            generated = helper.regenerate_entrypoints(directory, metadata.distributions(path=[str(directory)]))
            launcher = directory / "relocation-probe.exe"
            self.assertIn(str(launcher), generated)
            result = subprocess.run([str(launcher)], input=b'{"relocated":true}', capture_output=True, check=True)
            self.assertIn(b'"relocated": true', result.stdout)
