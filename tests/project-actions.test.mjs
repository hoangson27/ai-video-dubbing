import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'
import * as workflow from '../src/workflow.ts'

// Exercise the actual App handlers without mounting a WebView or touching real projects.
const require = createRequire(import.meta.url)
const { descriptor } = parse(readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8'))
const compiled = ts.transpileModule(compileScript(descriptor, { id: 'project-actions-test' }).content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function appState(failure) {
  const calls = []
  const native = { engine: async (_settings, action, args) => {
    calls.push({ action, ...args })
    if (failure) throw new Error(failure)
    return {}
  } }
  const module = { exports: {} }
  const imports = (name) => {
    if (name === 'vue') return { ...vue, onMounted() {}, onBeforeUnmount() {} }
    if (name === './workflow') return workflow
    if (name === './native') return native
    if (name === '@tauri-apps/api/core') return { isTauri: () => false }
    if (name === '@tauri-apps/api/event') return { listen: async () => () => {} }
    if (name.endsWith('.vue')) return {}
    return require(name)
  }
  new Function('require', 'module', 'exports', 'localStorage', compiled)(
    imports, module, module.exports, { getItem: () => null, setItem() {} },
  )
  const app = module.exports.default.setup({}, { expose() {} })
  const first = { id: 'a', source: 'D:\\first.mp4', title: 'First', status: 'ready', segments: [], options: {} }
  const second = { ...first, id: 'b', source: 'D:\\second.mp4', title: 'Second' }
  app.jobs.value = [first, second]
  app.currentJob.value = first
  app.source.value = first.source
  app.processingSource.value = first.source
  app.sourceUrl.value = 'blob:first-preview'
  app.drafts.value = { cue: 'An unsaved translation' }
  app.sourceDrafts.value = { cue: { text: 'Unsaved source', start: 0, end: 1 } }
  app.segmentAudio.value = { id: 'cue', url: 'blob:first-audio' }
  app.pendingByJob.set(second.id, { cue: 'Saved draft for Second' })
  return { app, calls, first, second }
}

const settled = () => new Promise(setImmediate)

test('deleting a list row targets the confirmed project and preserves the other open editor', async () => {
  const { app, calls, first, second } = appState()
  app.manageProject('delete_project', false, second)
  assert.equal(app.deleteConfirm.value.id, second.id)
  assert.deepEqual(calls, [])
  app.manageProject('delete_project', true)
  await settled()
  assert.deepEqual(calls, [{ action: 'delete_project', job_id: second.id }])
  assert.deepEqual(app.jobs.value.map(job => job.id), [first.id])
  assert.equal(app.currentJob.value.id, first.id)
  assert.equal(app.source.value, first.source)
  assert.equal(app.processingSource.value, first.source)
  assert.equal(app.sourceUrl.value, 'blob:first-preview')
  assert.equal(app.drafts.value.cue, 'An unsaved translation')
  assert.equal(app.sourceDrafts.value.cue.text, 'Unsaved source')
  assert.equal(app.segmentAudio.value.url, 'blob:first-audio')
  assert.equal(app.pendingByJob.has(second.id), false)
})

test('cancelled confirmation cannot fall back to deleting the currently selected project', async () => {
  const { app, calls, second } = appState()
  app.manageProject('delete_project', false, second)
  app.deleteConfirm.value = null
  app.manageProject('delete_project', true)
  await settled()
  assert.deepEqual(calls, [])
  assert.equal(app.jobs.value.length, 2)
})

test('deleting the selected project clears its previews and drafts only after success', async () => {
  const { app, calls, first, second } = appState()
  app.pendingByJob.set(first.id, { cue: 'Old draft' })
  app.manageProject('delete_project')
  app.manageProject('delete_project', true)
  await settled()
  assert.deepEqual(calls, [{ action: 'delete_project', job_id: first.id }])
  assert.deepEqual(app.jobs.value.map(job => job.id), [second.id])
  assert.equal(app.currentJob.value, null)
  assert.equal(app.source.value, '')
  assert.equal(app.processingSource.value, '')
  assert.equal(app.sourceUrl.value, '')
  assert.equal(app.segmentAudio.value, null)
  assert.deepEqual(app.drafts.value, {})
  assert.equal(app.pendingByJob.has(first.id), false)
  assert.equal(app.pendingByJob.has(second.id), true)
})

test('backend deletion failure retains the project, open editor and unsaved changes', async () => {
  const { app, calls, first, second } = appState('Recycle Bin unavailable')
  app.manageProject('delete_project', false, second)
  app.manageProject('delete_project', true)
  await settled()
  assert.deepEqual(calls, [{ action: 'delete_project', job_id: second.id }])
  assert.deepEqual(app.jobs.value.map(job => job.id), [first.id, second.id])
  assert.equal(app.currentJob.value.id, first.id)
  assert.equal(app.drafts.value.cue, 'An unsaved translation')
  assert.equal(app.pendingByJob.has(second.id), true)
  assert.equal(app.error.value, 'Recycle Bin unavailable')
})

test('project deletion cannot be requested while a pipeline is running', async () => {
  const { app, calls, second } = appState()
  app.activity.value = 'run'
  app.manageProject('delete_project', false, second)
  assert.equal(app.deleteConfirm.value, null)
  assert.deepEqual(calls, [])
})
