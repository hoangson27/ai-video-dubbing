# Project-local development environment

**Goal:** During development, all app-owned runtimes, AI packages/models, media tools, caches, logs, browser profile and video projects live under this checkout. The user explicitly requested this change; installer/release packaging is deferred.

**Approved layout:** `<repo>/.dev` is the dev data directory, retaining the current runtime/venv/models/tools/downloads/cache/logs/jobs/projects/index layout. Python is `.dev/runtime/python311-nuget`; AI libraries `.dev/venv`; FFmpeg `.dev/tools/ffmpeg/bin`; Ollama portable CLI `.dev/tools/ollama/ollama.exe`; its weights `.dev/models/ollama`. Node/npm live in `.dev/tools/node`. Existing Rust/MSVC/SDK toolchains stay in repo `.verification/toolchain`. Ignore `.dev` in Git and file watchers.

## Contracts

- Native debug builds always derive `.dev` from `CARGO_MANIFEST_DIR/..`, including direct `npx tauri dev`. Release path policy is left as-is.
- Native requests inject `settings.dev_mode=true`, `settings.dev_root=<repo>/.dev`, fixed project-owned executable/model/project paths. Persisted AppData/tool settings cannot redirect a dev worker outside this root.
- Shared process environment: `AIVD_DEV_MODE=1`, `AIVD_DEV_ROOT=<repo>/.dev`, `OLLAMA_HOST=127.0.0.1:11435`, `OLLAMA_MODELS=<root>/models/ollama`, `OLLAMA_NO_CLOUD=1`, local `PIP_CACHE_DIR`, `HF_HOME`, `TORCH_HOME`, `XDG_CACHE_HOME`, `UV_CACHE_DIR`, `NUMBA_CACHE_DIR`, `GRADIO_TEMP_DIR`, `TEMP` and `TMP`.
- For app workers/Ollama only, scope `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` to `.dev/profile` and children so Ollama keys/user caches do not use the real profile. Do not repurpose PowerShell `$HOME` or set the `HOME` environment variable. Native browser profile is explicitly `.dev/webview` and retains normal OS runtime discovery.
- Dev never discovers Python/FFmpeg/Ollama from PATH, registry, py launcher or global AppData. Dev Ollama uses port11435; release remains11434. Never reuse an unknown daemon just because its port is open.
- Native-owned daemon may be reused by a setup subprocess only with `settings.ollama_owned=true`, injected by native state (renderer input overwritten). Standalone setup must own its server or find the port free, otherwise report a clear busy error. Read-only inventory should not claim dev readiness against a foreign daemon.
- Python requests in dev sanitize effective settings after merging any persisted per-project settings. Resolve Ollama host strictly as IPv4 loopback + expected dev port; no remote/cloud endpoint.
- Existing inputs may be selected outside the checkout; app-owned outputs/runtime dependencies remain local. This does not relocate Windows, GPU drivers, system PowerShell or WebView2 runtime.
- All old user/source data is preserved during migration. Copy and validate before removing/replacing app-owned originals. Pre-existing shared Node/FFmpeg are retained as system software but not used by the dev app.

## Pinned portable downloads

- Python NuGet3.11.9: existing pin `9283876D58C017E0E846F95B490DA3BCA0FC0A6EE1134B2870677CFB7EEC3C67`.
- Ollama0.40.16 Windows AMD64 ZIP: `https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip`, SHA256 `50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e`; metadata captured from official API in `.verification/dev-ollama-release.json`.
- Node22.22.3 Windows AMD64 ZIP: `https://nodejs.org/dist/v22.22.3/node-v22.22.3-win-x64.zip`, SHA256 `6c8d54f635feff4df76c2ca80f45332eb2ff57d25226edce36592e51a177ee33`; official SHASUMS entry captured in `.verification/dev-node-sha256.txt`.
- FFmpeg retains the existing pinned archive/hash. Existing valid installed binaries can be copied once after signature/hash inventory, never invoked as the dev runtime from their global paths.

## Task ownership and verification

1. **Native agent:** `src-tauri/src/{lib,engine,profile,tests}.rs`, `engine/dependencies.rs`, any new profile module and config fields required for `.dev/webview`. Debug path and child environment, strict managed settings, local daemon ownership and port. Tests for debug roots, foreign path/daemon rejection and scoped environment; Rust tests/build.
2. **PowerShell agent:** `scripts/{install-deps,dependency-inventory,setup-deps,check-deps}.ps1`, optional shared `dev-profile.ps1` and its resource registration coordinated with native agent, PS tests. Strict local dev discovery, scoped caches/profile, portable Ollama ZIP (no system installer in dev), configurable loopback endpoint and owned-server flag, keep normal release behavior and portable Python security/lease. Test dev mode with poisoned global PATH/registry/settings and no external fallback.
3. **Python agent:** `engine/dubbing/{providers,pipeline,system_check}.py`, new profile helper, provider/profile tests; also `scripts/relocate-venv.py` if needed. Dev endpoint and settings isolation after merging snapshots; validate copied venv can run from the new base with no old-root dependency, regenerate console entry points using pip distlib. No user data/migration operations.
4. **Root:** `.gitignore`, Vite watcher, `scripts/dev.ps1`, Node bootstrap, actual migration, docs, final verification. Read old setup state; close only this app if needed; preserve downloaded/model assets; do not resume old global setup. Copy runtime, packages/models/cache/jobs/logs/downloads to `.dev`, copy FFmpeg and portable Ollama CLI/libs; recreate/repair venv at new path before use. Rewrite app-owned persisted paths to the new root, preserve source references and old records. Add local daemon receipt only for the verified local CLI.

## Acceptance

- One dev command `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1` sources the existing local Rust toolchain and local Node/npm.
- Direct Tauri dev launches also use local data/executables. Check/setup/inference commands use paths below the project and local Ollama models; no global fallback on missing tools.
- Migration keeps valid downloads/checkpoints and preserves source bytes. New venv reports the repo-local base interpreter, imports AI libraries from repo-local site-packages and invokes regenerated CLI entrypoints.
- Tests cover isolation despite hostile old C-drive settings; model service11434 is untouched/unreferenced by dev. A local setup/check completes on11435.
- Document exact `.dev` paths and the remaining OS prerequisites without claiming a self-contained release installer.

## Progress

- [x] Native debug profile and browser data
- [x] PowerShell dev setup/inventory and portable tools
- [x] Python profile, endpoint and venv relocation
- [x] Local launcher/Node bootstrap and migration
- [x] Local setup, inference smoke, reviews and docs

Migration verified 24,897 files (7,164,969,377 bytes); originals retained. Recreated the copied venv against `.dev/runtime/python311-nuget`, regenerated entry points and passed `pip check`. Local launcher diagnostics confirm Node/npm/Rust/Cargo and caches stay in the checkout. Final local Python/media suite: 86 tests, 84 passed and 2 opt-in live skips; frontend: 20 tests and build passed; native: 21 tests and debug build passed. Actual setup completed and inventory reports 18 installed, 0 missing. Native GUI confirmed local paths and readiness. Both real EN/ZH clips completed (86.235s/19.938s), verified unchanged source hashes and video payloads, all outputs under `.dev/projects`. Fixed source-audio tail padding and detection/repair of incomplete Whisper weights uncovered by live verification. Official repaired weights match SHA-256; backup preserved. Independent reviews found no remaining issues. README and testing guide updated; installer/release deferred.
