"""Repair copied Windows venv entry points after recreating its portable base.

Run with .dev/venv/Scripts/python.exe after the new portable Python has run
``-m venv .dev/venv``. This preserves installed packages and never imports
their entry point modules, downloads packages, or changes the original venv.
"""

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path


def validate_environment(dev_root: Path) -> tuple[Path, Path]:
    root = Path(dev_root).resolve()
    venv = root / "venv"
    base = root / "runtime" / "python311-nuget"
    if Path(sys.prefix).resolve() != venv or Path(sys.base_prefix).resolve() != base:
        raise ValueError("Run this helper using the recreated .dev/venv Python with its .dev/runtime/python311-nuget base.")
    scripts = venv / "Scripts"
    if Path(sys.executable).resolve() != scripts / "python.exe":
        raise ValueError("The active executable must be .dev/venv/Scripts/python.exe.")
    config = {}
    for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            config[key.strip().lower()] = value.strip()
    if config.get("include-system-site-packages", "").lower() != "false":
        raise ValueError("The copied venv must disable system site packages. Recreate it with the local portable Python.")
    home = config.get("home")
    if home and Path(home).resolve() != base:
        raise ValueError("pyvenv.cfg still references an old base. Recreate the venv with the local portable Python.")
    site = venv / "Lib" / "site-packages"
    if not site.is_dir() or not site.resolve().is_relative_to(root):
        raise ValueError("The copied site-packages directory must be inside .dev.")
    for pth in site.glob("*.pth"):
        if not pth.resolve().is_relative_to(root):
            raise ValueError(f"External .pth file: {pth}. Reinstall that package into .dev before continuing.")
        for line in pth.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("import ", "import\t")):
                # Standard setuptools import hooks are relative; embedded old drive paths are not.
                unsafe = bool(re.search(r"(?i)[a-z]:[\\/]", line))
            else:
                path = Path(line)
                unsafe = not (site / path).resolve().is_relative_to(root)
            if unsafe:
                raise ValueError(f"Unsafe external reference in {pth}: {line}. Reinstall that package into .dev before continuing.")
    return site, scripts


def regenerate_entrypoints(scripts: Path, distributions) -> list[str]:
    from pip._vendor.distlib.scripts import ScriptMaker

    maker = ScriptMaker(None, str(scripts))
    maker.executable = sys.executable
    maker.clobber = True
    maker.variants = {""}
    generated = []
    entries = [entry for distribution in distributions for entry in distribution.entry_points
               if entry.group in ("console_scripts", "gui_scripts")]
    for entry in entries:
        if not re.fullmatch(r"[\w.-]+", entry.name) or entry.name in (".", ".."):
            raise ValueError(f"Unsafe entry point name: {entry.name}")
    Path(scripts).mkdir(parents=True, exist_ok=True)
    for entry in entries:
        generated.extend(maker.make(f"{entry.name} = {entry.value}", {"gui": entry.group == "gui_scripts"}))
    return sorted(set(generated))


def relocate(dev_root: Path) -> list[str]:
    site, scripts = validate_environment(dev_root)
    return regenerate_entrypoints(scripts, metadata.distributions(path=[str(site)]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-root", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        generated = relocate(arguments.dev_root)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"Venv relocation refused: {error}\n")
    print(json.dumps({"python": sys.executable, "base_prefix": sys.base_prefix,
                      "prefix": sys.prefix, "scripts": generated}, indent=2))


if __name__ == "__main__":
    main()
