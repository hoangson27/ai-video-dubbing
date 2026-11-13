# Execution ledger — plan: docs/superpowers/plans/2026-11-11-project-workflow.md

2026-11-11: User explicitly approved proposal and requested dependency reset first, implementation next, then report ready for testing.

Ruling: implement in existing workspace because no Git repository exists; do not create unrelated repository/worktree.
Ruling: retain shared FFmpeg/system Python/Node/Rust and user artifacts; dependency reset concerns components installed specifically for the desktop app.
Ruling: leave AI runtime/models uninstalled at handoff so user can test one-button setup from the requested clean state. Verify inference-independent behavior with isolated fixtures; state real-inference validation limit.

| Shared boundary | Contract / review |
| --- | --- |
| Backend ↔ provider | translate existing/on_batch and translation_identity; callbacks persist accepted IDs before pause/failure |
| Backend ↔ frontend | old Job fields plus directory/stages/artifacts/exports; exact action names in plan |
| Backend ↔ native | project-index.json + .dubbing-project ownership + relative project paths; native import/source approval |
| Frontend ↔ native | engine_request actions + pause_engine + pick_artifact; project live event registered before emit |
| Reset ↔ setup | remove managed targets only; setup fresh default3B; preserved user settings need narrowly scoped migration |

Task 0: partial — managed dependency files/models and Ollama removed; audit `.verification/dependency-reset-20261111.json`. Python pip MSI removed but five remaining Python3.11 MSI registration records could not be uninstalled after interpreter removal. Official uninstall fails1601/1603/AppSearch. Automatic approval review rejected further elevated diagnostic reading due account quota; no bypass. Supported temporary official repair+uninstall remains blocked, no AI reinstall performed.

Task 1: complete — jobs/stages/project_edits/pipeline and19 initial project regressions, plus2 cached-identity regressions. Full Python suite63 total/55passed/8skipped; original user data untouched.
Task 2: complete — immediate batch callbacks, existing-ID reuse, failed-only retry/splitting, strict IDs, provider identity and per-stage devices, fresh3B default.
Task 3: complete — native approvals/ownership, external project media, pause, Explorer/RecycleBin/forget/temp cleanup, shared setup defaults, per-TTS device setup.14 Rust tests passed; native debug build passed. Official Python repair fallback fixture verified; actual recovery not run.
Task 4: complete — Vue projects, stage/manual/auto workflow, partial outputs, editing/imports/history/manage dialogs and settings. Browser mocked bridge QA7scenarios passed desktop/narrow/mobile, setup-log6scenarios passed. Final device migration preserves oldCPU/GPU preference and explicit stage choices;20UItests passed.
Task 5: integration/review complete and docs recorded. Fresh reviewer found/fixed durable-filename cleanup, identity promotion, tombstones, nested project and interrupted legacy issues; final scoped native/setup review found no material issue. Root verified63Python tests(55passed/8skipped),20UI,14Rust,13PowerShell fixture groups,frontend/nativebuild. Actual model inference/semantic comparison and full physical setup reset remain unverified in this pass.

Ruling: keep explicit configured7B while fresh settings default3B. Do not silently replace a user-selected model.
Ruling: native/browser QA is limited to native compilation, actual dependency inventory and mocked bridge browser scenarios; do not claim native GUI or real installer recovery/inference was retested.
Ruling: source relinking uses stored SHA256; an old project lacking a digest cannot claim a new source is identical.
Ruling: preserve cached translation provenance if current model digest is unavailable and provider/model/prompt still match; known changed digests continue to invalidate.
Ruling: retain original completed artifacts and export revisions; delete only owned generated scratch patterns and never a parent containing another project.
