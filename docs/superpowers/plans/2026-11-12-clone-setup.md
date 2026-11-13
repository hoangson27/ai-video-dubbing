# Fresh-clone setup implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement each task and verify its behavior.

**Goal:** A Windows x64 user can clone/download this repository, run `setup.cmd`, then `dev.cmd`, without an existing Node/Python/Rust/Visual Studio installation or this machine's ignored files.

**Architecture:** Track bootstrap source and small pinned artifact metadata under `scripts/toolchain/`; generated Rust/MSVC/SDK and Cargo caches remain in ignored `.verification/toolchain`, preserving existing working caches. App Python, Node, AI packages/models/cache remain in ignored `.dev`. `setup.cmd` calls a PowerShell orchestrator; `dev.cmd` retains the existing launcher. Full setup prepares npm and app dependencies; an explicit `-SkipAi` supports toolchain-only verification/UI development. Missing OS WebView2 is checked separately and can be installed per-user from Microsoft's signed official bootstrapper.

**Tech stack:** Windows PowerShell 5.1, portable CPython 3.11.9 standard library, Node 22.22.3, Rust 1.99.1 x64 MSVC, selected pinned Microsoft compiler/SDK payloads, Tauri 2/Vite/Vue.

**Spec:** The user's approved request to make a fresh clone installable/runnable and document installation, following the Git-readiness assessment.

## Global constraints

- No Git initialization, commit, remote, push, release build or installer publishing requested.
- Bootstrap source and payload versions/hashes are tracked; downloaded binaries, model weights, videos, credentials and reports remain ignored.
- Never execute SDK MSI installers: read verified MSI tables and extract selected CAB files locally. Do not edit machine PATH, registry, security policy or user shortcuts. OS WebView2 installation, if absent, is the sole documented per-user OS runtime exception.
- Keep the current user's running AI work intact: no venv mutation, model downloads, daemon restarts or app restarts for verification against the current live profile.
- Validate download hosts/hashes/signatures, extraction paths and existing-directory ownership; preserve installed tools on failed setup and support retry/reuse.
- Use UTF-8 BOM for Vietnamese Windows PowerShell source. No worktree can be created because this checkout has no Git metadata.

## Task 1: Reproducible local native toolchain

**Owner/files:** Native-bootstrap agent: `scripts/toolchain/bootstrap.py`, `scripts/toolchain/manifest.json`, `scripts/toolchain/env.ps1`, `scripts/tests/test_toolchain.py`.

**Interface:** `python bootstrap.py --repo-root ABS [--check] [--offline]`; optional contained `--cache-dir ABS`. Default native output is `<repo>/.verification/toolchain/{rust-local,msvc}`. The tracked environment script locates the repo from its own path and sets local Cargo/Rust/MSVC/SDK paths; keep compatible generated `env.ps1` if needed. Manifest selects official Rust tar.xz components, compiler/CRT VSIX, SDK MSI and required CAB URLs/hashes from verified existing metadata, without the full VS/Rust catalog.

- [x] Write failing fixture tests for cache verification/re-download, traversal/link rejection, complete Rust assembly, MSI/CAB mapping and partial failure without replacing valid tools.
- [x] Implement safe downloads/extraction, version checks and idempotent readiness; no global discovery/fallback.
- [x] Run focused tests and verify real pinned cached artifacts can provision a separate ignored proof directory.

## Task 2: OS WebView2 readiness

**Owner/files:** WebView agent: `scripts/webview2.ps1`, `scripts/tests/webview2.test.ps1`.

**Interface:** Dot-source without side effects; expose `Get-AivdWebView2Status` and `Install-AivdWebView2 -DataDir ABS`. Detect supported installed Microsoft runtime from documented per-user/machine metadata. If absent, obtain the official Microsoft Evergreen bootstrapper under `.dev/downloads`, validate Microsoft signature and run silent per-user install without elevation. Return a clear status; verification tests are offline/mocked and never change this machine's installed runtime.

- [x] Verify documented detection/install behavior against official sources and cover installed/missing/failed/signature paths.
- [x] Implement contained download/installer logging, no RunAs, persistent environment or policy changes.
- [x] Run focused PowerShell tests.

## Task 3: One-command setup and launch

**Owner/files:** Root: `setup.cmd`, `scripts/setup-dev.ps1`, a focused `scripts/bootstrap-python.ps1` if necessary, `scripts/dev.ps1`, `package.json`, `.gitignore`, setup orchestration tests.

- [x] Prepare tests for missing prerequisites, correct explicit local paths, failure propagation, no-op readiness and full-vs-SkipAi stages.
- [x] Bootstrap verified local Python and Node using existing portable installation helpers.
- [x] Invoke the tracked native bootstrap, source tracked environment, install npm with local Node/npm and local cache.
- [x] Detect/provision WebView2 using Task 2, then invoke existing app install/check scripts in a child process for full AI setup, preserving their ownership/lease contracts.
- [x] Default full setup prepares Whisper small/Qwen3B/VieNeu; `-SkipAi` explicitly defers AI setup to the app. `-CheckOnly` only inspects/report readiness and does not download/install.
- [x] Update dev launcher to use tracked environment source and provide actionable first-setup guidance or bootstrap missing build tools, avoiding beforeDevCommand recursion (`npm run dev` remains Vite).
- [x] Ignore environment secrets/signing keys and all generated toolchain/model/build output; retain both application lockfiles.

## Task 4: README and clean-source verification

**Owner/files:** Documentation agent: README and setup sections in `docs/testing.md`, coordinated against final CLI options.

- [x] Document Windows x64, Internet/storage and OS prerequisites, Git clone or ZIP, `setup.cmd`, `dev.cmd`, Setup UI alternative for SkipAi, diagnostic/retry commands, layout and no committed dependencies.
- [x] Replace machine-specific example paths and describe first-run downloads honestly. Do not choose a project license or claim unperformed VM/NSIS acceptance.
- [x] Copy only Git-eligible source into an ignored proof directory; seed download caches with verified official artifacts, without copying installed runtimes/toolchains or source videos as prerequisites.
- [x] Run bootstrap checks/tool installation and npm install/build from the isolated source copy, confirm all resolved paths stay in that copy; use relevant offline AI orchestration tests because the live user profile must not be modified.
- [x] Run focused regressions plus meaningful existing checks; independent review; update this ledger with actual evidence and remaining limits.

## Review focus

- A clean Windows account has no global Python/Node/Rust/MSVC, and its checkout path may include spaces/non-ASCII characters.
- A download/cache archive may be incomplete or have unsafe member names; a failed retry must not destroy valid tools or leave readiness falsely true.
- Installed SDK/CRT files must actually satisfy a Cargo native link, not merely be present.
- All nested PowerShell processes need the current working directory, local executable paths and preserved error codes.
- Setup must not mutate an in-use venv/model service; checking readiness is not installation, and release distribution remains deferred.

## Verification ledger

Completed 2026-11-13.

- Isolated source-only proof: `.verification/clone-bootstrap-proof`; raw verified Python/Node/Rust/VSIX/MSI/CAB archives seeded, no installed runtimes/native tools, `node_modules`, model data, Cargo extracted sources or compiled target copied as prerequisites.
- Real local Python 3.11.9 and Node 22.22.3 installations passed. Native bootstrap provisions Rust 1.99.1, MSVC 14.44.35207 and SDK 10.0.26100.0 from the compact 36-artifact manifest. Both C++ and Rust compiled/linked/ran under only local native bins and Windows System32.
- The complete `setup.cmd -SkipAi` controller installed 71 npm packages and returned ready=true with an initially Windows-only PATH. Final `setup.cmd -CheckOnly` returned ready=true. `dev.cmd -CheckOnly` resolved Node/npm/Rust/cache/model paths inside the proof checkout. The original checkout's launcher path inspection also passed without restarting any app/service.
- Proof frontend: 25/25 UI tests and production build passed. Fresh local Cargo target: actual Tauri debug build passed offline in 1m54s. Native library tests passed 21/21 outside the sandbox; the initial sandbox run's sole Windows5 error querying TCP ownership did not reproduce outside it. No GUI process was started.
- PowerShell fixtures: installer11 + inventory7 + dev-profile9 + controller5 + WebView2 10 + Python repair lease3 = 45 passed checkpoints. One real-junction branch in installer fixtures was sandbox-denied and used a simulated reparse fixture. Native bootstrap regression suite passed10/10 including a real Windows junction in a separate permitted execution.
- Independent review found and resolved two final P2 defects: malformed receipt JSON needed type validation before member access, and runtime repair needed an OpenOrCreate exclusive engine lease even when the lock file was absent. The Python race regression failed with the old source and passed after the fix. Final scoped review reports no further must-fix issues.
- Candidate source audit: 113 files, approximately1.4MB; downloaded binaries/models, video/audio, generated output/caches and signing keys/env files excluded. No Git repository was initialized, committed or pushed, and no license was chosen.
- Evidence: `.verification/clone-final-summary.json`, `clone-final-tests/`, `clone-proof-bootstrap-runtimes.log`, `clone-bootstrap-native.log`, `clone-proof-setup.log`, `clone-proof-check.json`, `clone-proof-launch-paths.json`, `clone-proof-ui-tests.log`, `clone-proof-frontend-build.log`, `clone-proof-cargo-build.log`, `clone-proof-cargo-test-unsandboxed.log`.

Limits: source-copy verification ran on the existing Windows host and reused its installed WebView2. Missing-WebView2 installation and full AI setup orchestration were covered by fixtures; no duplicate multi-GB model setup or mutation of the user's active AI environment was performed. No clean Windows VM, second GUI instance, release build or NSIS installer acceptance is claimed. README and docs/testing.md describe the two-command workflow and these limits.
