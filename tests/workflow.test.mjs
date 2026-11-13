import assert from 'node:assert/strict'
import test from 'node:test'
import * as workflow from '../src/workflow.ts'
import { engine } from '../src/native.ts'

const ready = { ready: true, checks: [], hardware: { gpus: [] }, voices: [{ id: 'preset', label: 'Preset' }] }
const source = { duration: 10, audio_tracks: [{ index: 1 }] }

test('does not allow processing until the current settings pass System Check', () => {
  assert.equal(workflow.processingBlock(null, source, 'preset'), 'Kiểm tra hệ thống trước khi bắt đầu.')
  assert.equal(workflow.processingBlock(ready, source, 'preset'), '')
})

test('blocks video without a usable audio track', () => {
  assert.equal(workflow.processingBlock(ready, { duration: 10, audio_tracks: [] }, 'preset'), 'Video này không có track âm thanh.')
})

test('rejects a preset removed by a model change', () => {
  assert.equal(workflow.processingBlock(ready, source, 'previous'), 'Chọn giọng đọc để tiếp tục.')
})

test('segment times keep milliseconds and preserve hours', () => {
  assert.equal(workflow.timestamp(3661.234), '01:01:01.234')
  assert.equal(workflow.timestamp(59.9996), '01:00.000')
})

test('only substantive translation changes block export', () => {
  assert.equal(workflow.hasDraftChange('  Xin chào\n', 'Xin chào'), false)
  assert.equal(workflow.hasDraftChange(undefined, 'Xin chào'), false)
  assert.equal(workflow.hasDraftChange(' ', 'Xin chào'), true)
  assert.equal(workflow.hasDraftChange('Chào bạn', 'Xin chào'), true)
})

test('dependency summary keeps unchecked prerequisites separate from missing software', () => {
  assert.deepEqual(workflow.dependencySummary([
    { status: 'installed' }, { status: 'installed' }, { status: 'missing' },
    { status: 'incompatible' }, { status: 'unchecked' },
  ]), { installed: 2, missing: 1, incompatible: 1, unchecked: 1 })
})

test('managed installation only applies executable paths returned by setup', () => {
  const current = { ...workflow.defaultSettings, ollama_model: 'qwen2.5:1.5b', output_dir: 'D:\\exports' }
  const updated = workflow.applyDependencySettings(current, {
    python_path: 'C:\\app\\venv\\Scripts\\python.exe',
    ffmpeg_path: 'C:\\app\\tools\\ffmpeg.exe',
    ffprobe_path: '',
    ollama_model: 'unrequested-model',
    output_dir: 'D:\\other',
  })
  assert.equal(updated.python_path, 'C:\\app\\venv\\Scripts\\python.exe')
  assert.equal(updated.ffmpeg_path, 'C:\\app\\tools\\ffmpeg.exe')
  assert.equal(updated.ollama_model, current.ollama_model)
  assert.equal(updated.output_dir, current.output_dir)
  assert.equal(current.python_path, '')
  assert.deepEqual(workflow.applyDependencySettings(current, undefined), current)
})

test('install button dispatches the native installer with current model settings', async () => {
  const previousWindow = globalThis.window
  let called
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async (...args) => { called = args; return ready } } }
  try {
    assert.equal(await engine(workflow.defaultSettings, 'install_dependencies', {}), ready)
    assert.equal(called[0], 'engine_request')
    assert.equal(called[1].request.action, 'install_dependencies')
    assert.equal(called[1].request.settings.ollama_model, 'qwen2.5:3b')
    assert.equal('verify_packets' in called[1].request.settings, false)
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})

const emptyInstallation = {
  status: 'idle', progress: 0, message: '', logs: [], error: '', cancelling: false, canCancel: false, logPath: '',
}

test('setup log restores the actual failure after restart and ignores intermediate installer results', () => {
  const restored = workflow.mergeSetupLog(emptyInstallation, {
    path: 'C:\\app\\logs\\setup-latest.log',
    lines: [
      JSON.stringify({ type: 'progress', progress: 11, message: 'Đang kiểm tra Python' }),
      'NameError: name P is not defined',
      JSON.stringify({ type: 'result', data: { settings_patch: { python_path: 'C:\\python.exe' } } }),
      JSON.stringify({ type: 'error', message: 'Python probe failed: NameError' }),
    ],
  }, false)
  assert.equal(restored.status, 'failed')
  assert.equal(restored.progress, 11)
  assert.equal(restored.error, 'Python probe failed: NameError')
  assert.ok(restored.logs.includes('NameError: name P is not defined'))
  assert.equal(restored.logPath, 'C:\\app\\logs\\setup-latest.log')
})

test('only final successful native readiness completes a restored setup', () => {
  const restored = workflow.mergeSetupLog(emptyInstallation, {
    path: 'setup.log', lines: [JSON.stringify({ type: 'result', data: { ready: true }, message: 'Thiết lập hoàn tất.' })],
  }, true)
  assert.equal(restored.status, 'completed')
  assert.equal(restored.progress, 100)
  assert.equal(restored.error, '')
  const incomplete = workflow.mergeSetupLog(emptyInstallation, {
    path: 'setup.log', lines: [JSON.stringify({ type: 'result', data: { ready: false } })],
  }, false)
  assert.equal(incomplete.status, 'failed')
  assert.ok(incomplete.error)
})

test('terminal setup error remains visible exactly once and logging is bounded', () => {
  const current = { ...emptyInstallation, status: 'running', logs: Array.from({ length: 500 }, (_, index) => `line ${index}`) }
  const failed = workflow.recordSetupError(current, 'Original installer error', false)
  assert.equal(failed.logs.length, 500)
  assert.equal(failed.logs.at(-1), 'Original installer error')
  assert.equal(failed.error, 'Original installer error')
  assert.equal(failed.canCancel, false)
  assert.equal(workflow.recordSetupError(failed, 'Original installer error', false).logs.length, 500)
})

test('a historical successful setup cannot override a fresh failed readiness check', () => {
  const saved = { path: 'setup.log', lines: [JSON.stringify({ type: 'result', data: { ready: true }, message: 'Previous setup succeeded' })] }
  for (const current of [emptyInstallation, { ...emptyInstallation, status: 'completed', progress: 100 }]) {
    const restored = workflow.mergeSetupLog(current, saved, false)
    assert.equal(restored.status, 'failed')
    assert.notEqual(restored.progress, 100)
    assert.ok(restored.error)
    assert.ok(restored.logs.includes('Previous setup succeeded'))
  }
})

test('settings migration preserves configured models and clears only managed Python paths', () => {
  const migrated = workflow.migrateSettings({
    ...workflow.defaultSettings, ollama_model: 'qwen2.5:7b',
    python_path: 'C:\\Users\\User\\AppData\\Local\\com.aivideodubbing.desktop\\venv\\Scripts\\python.exe',
    output_dir: 'D:\\kept', hardware_mode: 'cpu', whisper_model: 'base',
  }, 1)
  assert.equal(migrated.ollama_model, 'qwen2.5:7b')
  assert.equal(migrated.python_path, '')
  assert.equal(migrated.output_dir, 'D:\\kept')
  assert.equal(migrated.whisper_model, 'base')
  assert.equal(migrated.hardware_mode, 'cpu')
  assert.equal(workflow.migrateSettings({ ollama_model: 'qwen2.5:7b', python_path: 'D:\\my-venv\\python.exe' }, 2).ollama_model, 'qwen2.5:7b')
  assert.equal(workflow.migrateSettings({ ollama_model: 'custom:7b', python_path: 'D:\\my-venv\\python.exe' }, 1).python_path, 'D:\\my-venv\\python.exe')
  assert.equal(workflow.migrateSettings({}, 1).ollama_model, 'qwen2.5:3b')
})

test('legacy hardware preferences become per-stage defaults without replacing explicit stage choices', () => {
  for (const hardware_mode of ['cpu', 'gpu']) {
    const migrated = workflow.migrateSettings({ hardware_mode }, 1)
    assert.equal(migrated.hardware_mode, hardware_mode)
    assert.equal(migrated.asr_device, hardware_mode)
    assert.equal(migrated.translation_device, hardware_mode)
    assert.equal(migrated.tts_device, hardware_mode)
  }
  const explicit = workflow.migrateSettings({ hardware_mode: 'gpu', asr_device: 'cpu', translation_device: 'auto' }, 1)
  assert.equal(explicit.asr_device, 'cpu')
  assert.equal(explicit.translation_device, 'auto')
  assert.equal(explicit.tts_device, 'gpu')
})

const project = {
  id: 'p1', source: 'D:\\video.mp4', status: 'ready', stage: 'translate', mode: 'manual',
  segments: [{ id: 'a', text: 'Hello', start: 0, end: 1, translation: 'Xin chào' }],
  warnings: [], options: {}, artifacts: { transcript: 'D:\\project\\transcript.json' },
  stages: { probe: { status: 'ready' }, extract: { status: 'ready' }, transcribe: { status: 'ready' }, translate: { status: 'failed', completed: 2, total: 4 } },
}

test('manual stage execution requires its direct inputs to be current', () => {
  assert.equal(workflow.stageBlock(project, 'translate'), '')
  assert.match(workflow.stageBlock(project, 'synthesize'), /Dịch thoại/)
  assert.match(workflow.stageBlock({ ...project, stages: { ...project.stages, transcribe: { status: 'stale' } } }, 'translate'), /Nhận dạng/)
  assert.equal(workflow.stageBlock(project, 'probe'), '')
})

test('imported transcript can be translated without extracting audio again', () => {
  const imported = { ...project, stages: { probe: { status: 'ready' }, extract: { status: 'pending' }, transcribe: { status: 'ready', manual: true } } }
  assert.equal(workflow.stageBlock(imported, 'translate'), '')
  assert.match(workflow.stageBlock(imported, 'transcribe', true), /Tách âm thanh/)
  assert.equal(workflow.stageBlock(imported, 'transcribe'), '')
})

test('alignment and export use validated direct results rather than all previous stages', () => {
  const imported = { ...project, stages: { probe: { status: 'ready' }, synthesize: { status: 'ready' }, align: { status: 'ready' } } }
  assert.equal(workflow.stageBlock(imported, 'align', true), '')
  assert.equal(workflow.stageBlock(imported, 'mux'), '')
  assert.match(workflow.stageBlock({ ...imported, stages: { ...imported.stages, probe: { status: 'stale' } } }, 'mux'), /Đọc video/)
})

test('live project batches merge artifacts and preserve unsaved drafts by stable segment ID', () => {
  const next = workflow.mergeProjectUpdate(project, {
    id: 'p1', stages: { translate: { status: 'running', completed: 3, total: 4 } },
    artifacts: { translation: 'D:\\project\\translation.json' },
    segments: [...project.segments, { id: 'b', text: 'Bye', start: 1, end: 2, translation: 'Tạm biệt' }],
  })
  assert.equal(next.mode, 'manual')
  assert.equal(next.stages.probe.status, 'ready')
  assert.equal(next.artifacts.transcript, project.artifacts.transcript)
  assert.equal(next.stages.translate.completed, 3)
  assert.deepEqual(workflow.reconcileDrafts(project.segments, next.segments, { a: 'Chào bạn' }), { a: 'Chào bạn', b: 'Tạm biệt' })
  assert.deepEqual(workflow.reconcileDrafts(project.segments, [{ ...project.segments[0], translation: 'Bản mới' }], { a: 'Xin chào' }), { a: 'Bản mới' })
})

test('source edits validate finite timing against duration and preserve meaningful text', () => {
  assert.equal(workflow.sourceEditBlock({ text: ' Chào ', start: 0, end: 5 }, 10), '')
  assert.ok(workflow.sourceEditBlock({ text: ' ', start: 0, end: 5 }, 10))
  assert.ok(workflow.sourceEditBlock({ text: 'Chào', start: 5, end: 5 }, 10))
  assert.ok(workflow.sourceEditBlock({ text: 'Chào', start: 0, end: 11 }, 10))
  assert.ok(workflow.sourceEditBlock({ text: 'Chào', start: NaN, end: 5 }, 10))
})

test('project actions and artifact picker keep the native bridge contract', async () => {
  const previousWindow = globalThis.window
  const calls = []
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async (...args) => { calls.push(args); return project } } }
  try {
    await engine(workflow.defaultSettings, 'create_project', { source: project.source, options: {}, copy_source: true })
    await engine(workflow.defaultSettings, 'run_stage', { job_id: project.id, stage: 'translate', force: true })
    await engine(workflow.defaultSettings, 'update_project', { job_id: project.id, mode: 'manual', settings: { translation_device: 'cpu' } })
    const { pickArtifact, pauseEngine } = await import('../src/native.ts')
    await pickArtifact('translation')
    await pauseEngine()
    await engine(workflow.defaultSettings, 'open_project', { path: 'D:\\project' })
    await engine(workflow.defaultSettings, 'relink_source', { job_id: project.id, source: 'D:\\moved-video.mp4' })
    assert.equal(calls[0][1].request.copy_source, true)
    assert.equal(calls[1][1].request.force, true)
    assert.equal(calls[2][1].request.mode, 'manual')
    assert.deepEqual(calls[3].slice(0, 2), ['pick_artifact', { kind: 'translation' }])
    assert.deepEqual(calls[4].slice(0, 2), ['pause_engine', {}])
    assert.equal(calls[5][1].request.path, 'D:\\project')
    assert.equal(calls[6][1].request.source, 'D:\\moved-video.mp4')
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})
