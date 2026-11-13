# Project Workflow Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; root integrates the shared contracts and delegates independent files. Track completion here and in the execution ledger.

**Goal:** Reset app-installed dependencies, then ship folder-based video projects with durable stage results, auto/manual execution, partial translation recovery, model selection and project cleanup.

**Architecture:** Extend the current Python Jobs/Engine rather than replace providers. Python owns a versioned project manifest, durable stage runner and index. Rust validates native paths, forwards live artifact events and handles pause/recycle/open actions. Vue exposes those same stages and stored results.

**Tech Stack:** Existing Vue 3 / TypeScript / Tauri 2 / Rust / Python 3.11 / PowerShell 5.1; no new npm or Python packages.

**Spec:** `docs/superpowers/specs/2026-11-11-project-workflow-proposal.md`, approved by user 2026-11-11 with dependency reset first and concrete 3B/7B selection.

## Global constraints

- Reset only app-installed Python311/venv/model/download trees and Ollama; preserve shared FFmpeg, other Python versions, Node/Rust, user jobs/audio/transcripts/outputs and browser settings.
- Do not reinstall AI dependencies after reset: leave the machine ready for the user to test fresh automatic Setup.
- Work in the current workspace: no git repository exists (`git rev-parse` fails), so worktree/commit steps are unavailable.
- Preserve original files; project deletion goes to Windows Recycle Bin and rejects active/unsafe roots or reparse trees.
- Existing projects remain usable; migration copies artifacts, never moves/deletes legacy data automatically.
- All user-facing copy Vietnamese. Native setup/progress/error logs remain available.
- Newly initialized settings choose `qwen2.5:3b`; `qwen2.5:7b` remains selectable for comparison. Do not claim measured semantic equivalence or speed gains without real inference tests.

## Review focus

1. Interrupted translation after several valid batches: saved IDs survive; retries preserve correct ID associations and manual edits.
2. Source/timing/translation/model change: stale downstream output cannot be treated as current, and prior export revisions remain accessible.
3. Project rename/deletion/import: validate ownership, canonical paths, external source references and reparse points.
4. Pausing during a long model call: current unit finishes and persists before graceful pause; cancel remains a separate hard-stop path.
5. Clean dependency state: missing runtime does not crash inventory, setup offers selected model, no stale log success overrides current readiness.

## Shared contracts

Stage IDs in order: `probe`, `extract`, `transcribe`, `translate`, `synthesize`, `align`, `mux`.

`Settings` retains current fields and adds optional `projects_dir`, `asr_device`, `translation_device`, `tts_device` (`auto|cpu|gpu`). Native injects a default projects directory under Videos/AI Video Dubbing if unset; isolated Python tests default to data_dir/projects. Optional `Options.audio_track` selects ffprobe stream index.

Job view retains all old fields and adds `directory`, `title`, `mode` (`auto|manual`), `source_mode` (`reference|copy`), `metadata`, `stages`, `artifacts`, `exports`. Status adds `ready` and `paused`. Each stage record has `status` (`pending|running|ready|stale|failed|paused`), optional `key`, `error`, `completed`, `total`, `duration_ms`; artifacts is a map of labels to absolute paths in public views. Stored project-owned paths are relative. Export entries contain `path`, `created`, `revision`.

New Python engine actions:

- `create_project {source, options, copy_source?: boolean}` -> Job; repeated reference to same unchanged source reopens project unless `new_project: true`.
- `run_stage {job_id, stage, force?: boolean, verify_packets?: boolean}` -> Job; uses valid prerequisites, executes selected stage only.
- `run {job_id, mode?: auto|manual, stop_after?: StageId, verify_packets}` -> Job; old source/options run remains compatible.
- `update_project {job_id, options?: Partial<Options>, settings?: Partial<Settings>, mode?: auto|manual}` -> Job; persisted project settings and dependency invalidation.
- `edit_source_segment {job_id, segment_id, text, start, end}` -> Job; source text/time edits validated against duration.
- `import_artifact {job_id, stage: extract|transcribe|translate|synthesize, path, segment_id?: string}` -> Job; native path must be approved through artifact picker.
- `migrate_project {job_id}` -> Job; explicit copy migration of legacy jobs.
- Old edit/retranslate/regenerate/export/get/list actions retain signatures.

Native-only actions through `engine_request`:

- `open_project_folder`, `open_project_output`, `forget_project`, `delete_project`, `clean_project` with `{job_id}` -> `{path?: string}`. Root validates `project-index.json` and manifest; no Python needed.
- `pause_engine` Tauri command creates sibling `.pause` marker for active engine request; injected Python `pause_path` checked at safe boundaries. Returns final Job status paused without treating it as failure. Cancellation behavior unchanged.
- `pick_artifact {kind: audio|transcript|translation}` native command returns approved local WAV/SRT/JSON path or null.

Project storage root index: `data_dir/project-index.json` = `{"version":1,"projects":{"<id>":"<absolute folder>"}}`. Ownership marker `<project>/.dubbing-project` = `{"version":1,"id":"<id>"}`. Manifest `<project>/project.json`. Existing `data_dir/jobs/<id>/job.json` is legacy fallback. Local folders follow proposal's 01-source through 07-output and logs layout. Public artifact map uses keys `source_audio`, `transcript`, `translation`, `dubbed` when available. Project view includes `directory` for native media validation.

Live event: `{type:'project', data: Job}` emitted on creation, stage completion, translation batch, and bounded TTS updates. Rust forwards and registers approved project media before emitting. Existing progress/log events retained.

Translator extension: `translate(segments, options, emit, target_id=None, *, existing=None, on_batch=None) -> dict[str,str]`; callback `on_batch(valid_translations: dict[str,str])` runs as soon as validated results exist. `translation_identity(settings) -> dict` includes resolved local model name/digest and prompt version; consumers can mock in isolated tests. Keep compatibility with old provider mocks through updating tests, not by swallowing runtime TypeErrors.

## Task 0 — dependency reset (root)

Files: `.verification/reset-app-deps.ps1`, `.verification/dependency-reset-20261111.json`.

- [x] Check exact ownership, signatures, process paths, manifest list and reparse-free target/ancestor paths.
- [x] Close owned app/processes, invoke signed official Ollama uninstaller, remove exact managed dependency targets.
- [x] Verify absence of managed dependencies, preserved job/source/cache/profile paths and shared tools; record audit. Do not reinstall.

## Task 1 — durable projects and stage engine (backend implementer)

Files: `engine/dubbing/jobs.py`, `pipeline.py`, new focused project/stage helper files as needed, `media.py`, `engine/tests/test_workflow.py`, new `engine/tests/test_projects.py`.

- [x] Write/run failing tests for folder-owned artifacts, pure read loading, paused/manual stage resume, downstream invalidation, export revisions, partial translation recovery and legacy copy migration.
- [x] Implement shared storage contract, canonical relative serialization and per-stage validated result commits.
- [x] Refactor run/export into stage functions; strict prerequisites; preserve successful artifacts during retries. Stale results remain viewable but cannot publish as current.
- [x] Persist each callback translation batch and expose current results; preserve edits; use translator identity in signatures. TTS cache hits materialize independent WAV copies inside project.
- [x] Add safe pause checks after persisted batches/segments/stages; expose source/timing edits and validated SRT/JSON/WAV imports.
- [x] Keep source preview/metadata, source audio, transcript, translation, voice, aligned audio and versioned outputs in project. No destructive migration.
- [x] Run Python unit tests using existing system Python (not the removed managed runtime); keep live inference tests skipped by default.

## Task 2 — translator reliability and device choices (provider implementer)

Files: `engine/dubbing/providers.py`, `system_check.py`, `engine/tests/test_providers.py`, device-check tests. Do not edit pipeline/jobs/frontend/setup scripts.

- [x] Write/run failing tests for existing-ID resume, partial callback retention, retry-only-failed IDs, split fallback, duplicate/extra ID rejection and model identity.
- [x] Extend translate signature above; maintain context and stable IDs; bounded retries and clear per-batch progress. Preserve malformed payload rejection and cloud safeguards.
- [x] Add identity helper, nonempty schema constraint and deterministic temperature; capture timing/token info without logging source content.
- [x] Respect per-stage device settings with old global fallback. Keep lazy TTS model load and release Ollama VRAM between stages.
- [x] Set provider fresh default to 3B. Do not run/download real models after reset.

## Task 3 — native project operations and setup defaults (root)

Files: `src-tauri/src/{lib,engine,tests}.rs`, new `projects.rs`, `Cargo.toml` only if Windows API features required, `scripts/{install-deps,dependency-inventory,setup-deps}.ps1`, setup tests.

- [x] Write/run meaningful tests for project ownership/path validation, approved import paths, artifact media registration, pause marker lifecycle and safe project operations.
- [x] Add new action allowlist and timeouts, native pause and artifact picker, inject default project root; register project audio outside app data only when owned/approved.
- [x] Implement Explorer/open-output, forget, Recycle Bin deletion and narrow temporary cleanup with active-engine exclusion and no follow of reparse points.
- [x] Switch fresh setup default to 3B consistently; preserve configured 7B choice; account for stale managed Python paths in inventory/installer.
- [x] Forward live project results; require approved source for create/relink and approved native-picked files for imports.

## Task 4 — Vue project/stage workflow (frontend implementer)

Files: `src/{workflow,native,App}.ts/vue`, `src/components/{ProcessMonitor,TranscriptEditor,SettingsForm,VideoSource,SystemCheck}.vue`, new stage/project components, existing styles, `tests/workflow.test.mjs`.

- [x] Write/run failing state tests for stages/manual mode/partial project updates/model settings migration.
- [x] Add typed shared contract and actions. Update selection to create/reopen project; provide auto/manual controls and per-stage run/view/retry/stop-after.
- [x] Show live intermediate results and count-based progress; safe pause/resume; source/timing edits, translation edits, audio preview and artifact import.
- [x] Add folder/output/forget/delete/clean controls with app confirmation for Recycle Bin; preserve drafts and prior export history. Paginate long transcript lists.
- [x] Expose project root, copy source, 3B/7B selection, independent devices; reset only stale managed Python path on settings migration, retain other user settings.
- [x] Build Vue/TS and run workflow tests; browser QA with mocked bridge may exercise data/UI without reinstalling AI.

## Task 5 — integration, review and verification (root + fresh reviewer)

- [x] Integrate concurrent files and reconcile shared contracts with tests; update README and docs/testing with exact verified behavior and limits.
- [x] Run Python suite, UI suite/build, PowerShell setup/inventory regressions, Rust tests/native dev build with repository toolchain.
- [x] Perform native/browser UI QA for clean missing-dependency screen, project stage outputs and manual edits without real model downloads; use generated fixtures only.
- [x] Fresh code review of deletion boundaries, interrupted translation, stale results, pause and legacy migration; fix material findings and rerun relevant tests.
- [x] Leave actual app dependencies absent for user's Setup test, report exact dev command and concise test checklist. No claim of real 3B semantic validation without inference.

## Remaining external block

- [ ] Finish removal of five app-owned Python3.11 MSI registration records through supported official repair/uninstall when automatic approval review is available. Dependency files/models remain absent; do not report a fully clean Windows Installer state.
