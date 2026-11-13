# Dependency check and installation implementation plan

**Goal:** Show a complete inventory before Python is available and let the user install the dependencies needed to run the application with one button.

**Architecture:** A packaged PowerShell inventory runs through the existing Rust process manager independently of Python. An explicit install action uses a packaged PowerShell installer, streams JSON Lines events, and returns discovered paths. Rust reruns inventory after installation. Vue displays all checks, installation progress, cancellation and failures.

**Constraints:** Preserve Python 3.11 as the tested AI runtime. Reuse existing compatible tools, install the runtime and FFmpeg in app-managed directories, keep Python AI libraries in a venv, and use only official download sources. Starting installation is the user's consent to dependency and model downloads. Do not modify Windows security policy or install system-wide software. A failed or cancelled installer must not claim readiness.

- [x] Independent inventory: enumerate available Python versions, required runtime/venv/packages, FFmpeg/FFprobe, Ollama/service/Qwen, Whisper and VieNeu models, and hardware. Mark blocked checks as unchecked. Merge a real Python report when available.
- [x] Installer: explicit install flag, missing-only acquisition, signature/hash validation, venv and selected model setup, idempotent retry, JSONL progress and cancellation. Return the three configured executable paths.
- [x] Native integration: route check without Python, expose install_dependencies via the existing command, inject trusted engine/data paths, share one active-process lease, preserve Job Object cancellation, and retain a managed Ollama service only after explicit setup.
- [x] UI: one install action with clear scope, complete statuses, live progress/logs, cancel and retry; apply returned paths and refresh readiness.
- [x] Verification: PowerShell fixture tests and real inventory on the current Python3.12-only machine; TypeScript/build/workflow tests; Rust check/tests if host policy permits; native UI smoke with controlled setup fixtures. Never silently install real software during automated tests.

Review cases: no Python at all; Python3.12 without3.11; stale configured paths; installed Ollama that is stopped; cancellation during download or pip; install succeeds but final check is not ready; retry after partial installation; installed resources versus developer paths.

- [ ] Manual acceptance: full vendor downloads and installations on a clean Windows account, then process a real video after setup.
