<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { isTauri } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { UnlistenFn } from '@tauri-apps/api/event'
import StudioIcon from './components/StudioIcon.vue'
import VideoSource from './components/VideoSource.vue'
import ProcessMonitor from './components/ProcessMonitor.vue'
import SettingsForm from './components/SettingsForm.vue'
import SystemCheck from './components/SystemCheck.vue'
import TranscriptEditor from './components/TranscriptEditor.vue'
import { cancelEngine, engine, mediaUrl, pauseEngine, pickArtifact, pickDirectory, pickVideo } from './native'
import { applyDependencySettings, defaultSettings, hasDraftChange, mergeProjectUpdate, mergeSetupLog, migrateSettings, processingBlock, reconcileDrafts, recordSetupError, sourceEditBlock, stageBlock } from './workflow'
import type { CheckReport, DependencyInstallation, EngineEvent, Job, Metadata, Options, ProjectMode, Segment, Settings, SourceDraft, StageId } from './workflow'

const pages = [
  { id: 'home', label: 'Bàn dựng', icon: 'film', title: 'Lồng tiếng video', description: 'Chọn video nguồn, thiết lập bản dịch và giọng đọc tiếng Việt.' },
  { id: 'processing', label: 'Dự án', icon: 'activity', title: 'Dự án video', description: 'Xem kết quả từng bước, tiếp tục xử lý và mở các phiên bản đã xuất.' },
  { id: 'editor', label: 'Lời thoại', icon: 'lines', title: 'Chỉnh lời thoại', description: 'Rà soát bản dịch, nghe từng đoạn và xuất lại video.' },
  { id: 'system', label: 'Hệ thống', icon: 'pulse', title: 'Kiểm tra hệ thống', description: 'Xem tình trạng phần mềm, mô hình và khả năng tăng tốc trên máy này.' },
  { id: 'settings', label: 'Cài đặt', icon: 'gear', title: 'Cài đặt', description: 'Chọn công cụ, mô hình và thư mục lưu video.' },
] as const
type Page = (typeof pages)[number]['id']
type Activity = 'check' | 'install' | 'select' | 'probe' | 'proxy' | 'voice' | 'run' | 'jobs' | 'edit' | 'export' | 'directory' | 'audio' | 'project'

const languages = [
  ['auto', 'Tự động nhận diện'], ['zh', 'Tiếng Trung'], ['en', 'Tiếng Anh'],
  ['ja', 'Tiếng Nhật'], ['ko', 'Tiếng Hàn'], ['vi', 'Tiếng Việt'],
  ['fr', 'Tiếng Pháp'], ['de', 'Tiếng Đức'], ['es', 'Tiếng Tây Ban Nha'],
] as const
const deviceFields = [
  { key: 'asr_device', label: 'Nhận dạng' }, { key: 'translation_device', label: 'Dịch thoại' }, { key: 'tts_device', label: 'Tạo giọng' },
] as const

const storageKey = 'dubbing.settings.v1'
function loadSettings(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '{}') as Partial<Settings>
    const migrated = migrateSettings(saved, Number(localStorage.getItem('dubbing.settings.version') || 1))
    localStorage.setItem(storageKey, JSON.stringify(migrated))
    localStorage.setItem('dubbing.settings.version', '2')
    return migrated
  } catch {
    return { ...defaultSettings }
  }
}

const settings = ref(loadSettings())
const settingsDraft = ref({ ...settings.value })
const settingsDirty = computed(() => JSON.stringify(settings.value) !== JSON.stringify(settingsDraft.value))
const options = ref<Options>({ source_language: 'auto', voice: '', speed: 1, style: '', glossary: '' })
const copySource = ref(false)
const mode = ref<ProjectMode>('auto')
const stopAfter = ref<StageId | ''>('')
const selectedStage = ref<StageId>('probe')
const projectSettings = ref<Settings>({ ...settings.value })
const pausing = ref(false)
const resultMedia = ref<{ path: string; url: string; video: boolean } | null>(null)
const deleteConfirm = ref<Job | null>(null)
const report = ref<CheckReport | null>(null)
const tab = ref<Page>('system')
const currentPage = computed(() => pages.find((page) => page.id === tab.value) ?? pages[0])
const activity = ref<Activity | null>(null)
const busy = computed(() => activity.value !== null)
const ready = computed(() => report.value?.ready ?? false)
const source = ref('')
const metadata = ref<Metadata | null>(null)
const sourceUrl = ref('')
const usingProxy = ref(false)
const videoError = ref(false)
const voiceUrl = ref('')
const segmentAudio = ref<{ id: string; url: string } | null>(null)
const currentJob = ref<Job | null>(null)
const jobs = ref<Job[]>([])
const drafts = ref<Record<string, string>>({})
const pendingByJob = new Map<string, Record<string, string>>()
const sourceDrafts = ref<Record<string, SourceDraft>>({})
const pendingSourceByJob = new Map<string, Record<string, SourceDraft>>()
const editorDirty = computed(() => currentJob.value?.segments.some((segment) => hasDraftChange(drafts.value[segment.id], segment.translation)) ?? false)
const sourceDirty = computed(() => currentJob.value?.segments.some((segment) => {
  const draft = sourceDrafts.value[segment.id]
  return draft && (draft.text !== segment.text || draft.start !== segment.start || draft.end !== segment.end)
}) ?? false)
const projectDirty = computed(() => !!currentJob.value && (
  JSON.stringify(options.value) !== JSON.stringify(currentJob.value.options)
  || ['ollama_model', 'asr_device', 'translation_device', 'tts_device'].some((key) => projectSettings.value[key as keyof Settings] !== (currentJob.value?.settings?.[key as keyof Settings] ?? settings.value[key as keyof Settings]))
))
const progress = ref(0)
const stage = ref('')
const processMessage = ref('')
const processingSource = ref('')
const logs = ref<string[]>([])
const cancelling = ref(false)
const canCancel = ref(false)
const installation = ref<DependencyInstallation>({
  status: 'idle', progress: 0, message: '', logs: [], error: '', cancelling: false, canCancel: false, logPath: '',
})
const error = ref('')
const hint = ref('')
const unlisten: UnlistenFn[] = []
let disposed = false

const basename = (path: string) => path.split(/[\\/]/).at(-1) || path
const filename = computed(() => basename(source.value))
const startBlock = computed(() => processingBlock(report.value, metadata.value, options.value.voice))

function navigate(page: Page) {
  tab.value = page
}

function errorMessage(cause: unknown) {
  return typeof cause === 'string' ? cause : cause instanceof Error ? cause.message : 'Đã xảy ra lỗi khi chạy tác vụ.'
}

async function perform(kind: Activity, task: () => Promise<void>) {
  if (busy.value) return
  activity.value = kind
  error.value = ''
  hint.value = ''
  try {
    await task()
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    activity.value = null
  }
}

async function loadHistory() {
  jobs.value = await engine(settings.value, 'list_jobs', {})
}

async function checkSystem(openHomeWhenReady = false) {
  await perform('check', async () => {
    if (report.value) report.value = { ...report.value, ready: false }
    if (!isTauri()) {
      hint.value = 'Mở ứng dụng bằng Tauri để kiểm tra máy và xử lý video.'
      return
    }
    report.value = await engine(settings.value, 'check', {})
    installation.value = mergeSetupLog(installation.value, report.value.setup_log, report.value.ready)
    const voices = report.value.voices
    if (!voices.some((voice) => voice.id === options.value.voice)) options.value.voice = voices[0]?.id ?? ''
    voiceUrl.value = ''
    try {
      await loadHistory()
    } catch (cause) {
      if (ready.value) error.value = errorMessage(cause)
    }
    if (ready.value) {
      localStorage.setItem('dubbing.seenSystemCheck', 'yes')
      if (openHomeWhenReady && tab.value === 'system') navigate('home')
    }
  })
}

function installDependencies() {
  if (!isTauri() || !report.value || ready.value) return
  void perform('install', async () => {
    installation.value = {
      status: 'running', progress: 0, message: 'Đang chuẩn bị cài đặt', logs: [], error: '', cancelling: false, canCancel: false,
      logPath: report.value?.setup_log?.path || installation.value.logPath,
    }
    navigate('system')
    try {
      const installed = await engine(settings.value, 'install_dependencies', {})
      installation.value.canCancel = false
      settings.value = applyDependencySettings(settings.value, installed.settings_patch)
      settingsDraft.value = applyDependencySettings(settingsDraft.value, installed.settings_patch)
      localStorage.setItem(storageKey, JSON.stringify(settings.value))
      report.value = installed
      const voices = installed.voices
      if (!voices.some((voice) => voice.id === options.value.voice)) options.value.voice = voices[0]?.id ?? ''
      voiceUrl.value = ''
      if (installed.ready) installation.value.progress = 100
      installation.value.status = installed.ready ? 'completed' : 'failed'
      installation.value.message = installed.ready
        ? 'Cài đặt hoàn tất. Hệ thống sẵn sàng lồng tiếng.'
        : 'Đã cài đặt. Một số thành phần cần được kiểm tra lại.'
      installation.value = mergeSetupLog(installation.value, installed.setup_log, installed.ready)
      if (installed.ready) localStorage.setItem('dubbing.seenSystemCheck', 'yes')
    } catch (cause) {
      installation.value = recordSetupError(installation.value, errorMessage(cause), installation.value.cancelling)
      try {
        installation.value = mergeSetupLog(installation.value, await engine(settings.value, 'get_setup_log', {}), false)
      } catch { /* Keep the live log if reading its persisted copy fails. */ }
      try {
        report.value = await engine(settings.value, 'check', {})
        installation.value = mergeSetupLog(installation.value, report.value.setup_log, report.value.ready)
      } catch {
        if (report.value) report.value = { ...report.value, ready: false }
      }
      if (installation.value.status !== 'cancelled' && !report.value?.ready) throw cause
    } finally {
      installation.value.cancelling = false
      installation.value.canCancel = false
    }
  })
}

async function cancelInstallation() {
  if (activity.value !== 'install' || !installation.value.canCancel || installation.value.cancelling) return
  installation.value.cancelling = true
  try {
    await cancelEngine()
  } catch (cause) {
    installation.value.error = errorMessage(cause)
    installation.value.cancelling = false
  }
}

async function inspectSource(path: string) {
  navigate('home')
  if (currentJob.value) rememberDrafts(currentJob.value)
  currentJob.value = null
  drafts.value = {}
  sourceDrafts.value = {}
  source.value = path
  metadata.value = null
  sourceUrl.value = ''
  usingProxy.value = false
  videoError.value = false
  activity.value = 'probe'
  try {
    sourceUrl.value = await mediaUrl(path)
  } catch {
    videoError.value = true
  }
  const job = await engine(settings.value, 'create_project', { source: path, options: { ...options.value }, copy_source: copySource.value })
  showJob(job)
  source.value = job.source
  metadata.value = job.metadata ?? await engine(settings.value, 'probe', { source: path })
  selectedStage.value = 'probe'
}

function chooseVideo() {
  void perform('select', async () => {
    const path = await pickVideo()
    if (path) await inspectSource(path)
  })
}

function preparePreview() {
  void perform('proxy', async () => {
    const { path } = await engine(settings.value, 'prepare_preview', { source: source.value })
    sourceUrl.value = await mediaUrl(path)
    usingProxy.value = true
    videoError.value = false
  })
}

function previewVoice() {
  if (!ready.value || !options.value.voice) return
  void perform('voice', async () => {
    voiceUrl.value = ''
    const { voice, speed } = options.value
    const { path } = await engine(settings.value, 'preview_voice', { voice, speed })
    voiceUrl.value = await mediaUrl(path)
  })
}

function rememberDrafts(job: Job) {
  const changed = job.segments.filter((segment) => hasDraftChange(drafts.value[segment.id], segment.translation))
  if (changed.length) {
    pendingByJob.set(job.id, Object.fromEntries(changed.map((segment) => [segment.id, drafts.value[segment.id]!])))
  } else {
    pendingByJob.delete(job.id)
  }
  pendingSourceByJob.set(job.id, Object.fromEntries(job.segments.flatMap((segment) => {
    const draft = sourceDrafts.value[segment.id]
    return draft && (draft.text !== segment.text || draft.start !== segment.start || draft.end !== segment.end) ? [[segment.id, draft]] : []
  })))
}

function storeJob(job: Job) {
  const old = currentJob.value
  if (old && old.id !== job.id) rememberDrafts(old)
  if (old?.id === job.id) job = mergeProjectUpdate(old, job)
  const existing = old?.id === job.id ? drafts.value : pendingByJob.get(job.id)
  drafts.value = reconcileDrafts(old?.id === job.id ? old.segments : [], job.segments, existing ?? {})
  const savedSource = old?.id === job.id ? sourceDrafts.value : pendingSourceByJob.get(job.id)
  sourceDrafts.value = Object.fromEntries(job.segments.map((segment) => {
    const previous = old?.id === job.id ? old.segments.find((item) => item.id === segment.id) : undefined
    const draft = savedSource?.[segment.id]
    const dirty = draft && (!previous || draft.text !== previous.text || draft.start !== previous.start || draft.end !== previous.end)
    return [segment.id, dirty ? draft : { text: segment.text, start: segment.start, end: segment.end }]
  }))
  currentJob.value = job
  const index = jobs.value.findIndex((item) => item.id === job.id)
  if (index < 0) jobs.value.unshift(job)
  else jobs.value[index] = job
}

function showJob(job: Job) {
  storeJob(job)
  segmentAudio.value = null
  stage.value = job.stage
  progress.value = job.status === 'completed' ? 100 : 0
  processMessage.value = ''
  logs.value = job.logs ? [...job.logs] : []
  processingSource.value = job.source
  source.value = job.source
  metadata.value = job.metadata ?? null
  options.value = { ...job.options }
  mode.value = job.mode ?? 'auto'
  projectSettings.value = { ...settings.value, ...job.settings }
  resultMedia.value = null
  selectedStage.value = (['probe', 'extract', 'transcribe', 'translate', 'synthesize', 'align', 'mux'].includes(job.stage) ? job.stage : 'probe') as StageId
}

function openJob(job: Job) {
  void perform('jobs', async () => {
    showJob(await engine(settings.value, 'get_job', { job_id: job.id }))
    navigate('processing')
    try { sourceUrl.value = await mediaUrl(job.source); videoError.value = false } catch { sourceUrl.value = ''; videoError.value = true }
  })
}

function refreshHistory() {
  void perform('jobs', loadHistory)
}

function processVideo(_retry = false) {
  const saved = currentJob.value
  if (!ready.value || !saved || editorDirty.value || sourceDirty.value || projectDirty.value) return
  if (mode.value === 'manual') { navigate('processing'); runStage(selectedStage.value); return }
  void perform('run', async () => {
    segmentAudio.value = null
    processingSource.value = saved?.source || source.value
    progress.value = 0
    stage.value = ''
    processMessage.value = 'Đang khởi động engine'
    logs.value = []
    cancelling.value = false
    canCancel.value = false
    pausing.value = false
    navigate('processing')
    try {
      const verify_packets = settings.value.verify_packets
      const job = await engine(settings.value, 'run', { job_id: saved.id, verify_packets, mode: mode.value, ...(stopAfter.value ? { stop_after: stopAfter.value } : {}) })
      canCancel.value = false
      showJob(job)
      if (job.status === 'completed') navigate('editor')
    } catch (cause) {
      canCancel.value = false
      processMessage.value = cancelling.value ? 'Đã gửi yêu cầu hủy xử lý.' : 'Tác vụ đã dừng.'
      try {
        await loadHistory()
        const resumed = jobs.value.find((item) => item.id === saved.id)
        if (resumed) showJob(await engine(settings.value, 'get_job', { job_id: resumed.id }))
      } catch {
        // Keep the engine failure even if the checkpoint cannot be reopened.
      }
      if (currentJob.value?.status !== 'cancelled') throw cause
      hint.value = 'Đã hủy xử lý. Có thể mở lại công việc để tiếp tục.'
    } finally {
      cancelling.value = false
      pausing.value = false
    }
  })
}

async function cancel() {
  if (!canCancel.value || cancelling.value || (activity.value !== 'run' && activity.value !== 'export')) return
  cancelling.value = true
  try {
    await cancelEngine()
  } catch (cause) {
    error.value = errorMessage(cause)
    cancelling.value = false
  }
}

async function pause() {
  if (pausing.value || cancelling.value || (activity.value !== 'run' && activity.value !== 'export')) return
  pausing.value = true
  try {
    await pauseEngine()
    hint.value = 'Đang chờ nhóm hoặc đoạn hiện tại hoàn tất và lưu trước khi tạm dừng.'
  } catch (cause) {
    error.value = errorMessage(cause)
    pausing.value = false
  }
}

function runStage(id: StageId, force = false) {
  const job = currentJob.value
  if (!job || !ready.value || editorDirty.value || sourceDirty.value || projectDirty.value) return
  const blocked = stageBlock(job, id, force)
  if (blocked) { hint.value = blocked; return }
  void perform('run', async () => {
    selectedStage.value = id
    stage.value = id
    progress.value = 0
    logs.value = []
    processMessage.value = 'Đang chuẩn bị chạy bước đã chọn'
    canCancel.value = true
    cancelling.value = false
    pausing.value = false
    resultMedia.value = null
    navigate('processing')
    try {
      const updated = await engine(settings.value, 'run_stage', { job_id: job.id, stage: id, force, verify_packets: settings.value.verify_packets })
      storeJob(updated)
      processMessage.value = updated.status === 'paused' ? 'Đã lưu và tạm dừng.' : 'Đã lưu kết quả bước này.'
    } catch (cause) {
      try { storeJob(await engine(settings.value, 'get_job', { job_id: job.id })) } catch { /* Preserve the original stage error. */ }
      if (currentJob.value?.status !== 'cancelled') throw cause
    } finally {
      canCancel.value = false
      cancelling.value = false
      pausing.value = false
    }
  })
}

function updateProject(nextMode?: ProjectMode) {
  const job = currentJob.value
  if (!job) return
  void perform('project', async () => {
    const patch = nextMode ? { mode: nextMode } : {
      options: { ...options.value },
      settings: {
        ollama_model: projectSettings.value.ollama_model,
        asr_device: projectSettings.value.asr_device,
        translation_device: projectSettings.value.translation_device,
        tts_device: projectSettings.value.tts_device,
      },
    }
    const updated = await engine(settings.value, 'update_project', { job_id: job.id, ...patch })
    storeJob(updated)
    if (nextMode) mode.value = nextMode
    else {
      options.value = { ...updated.options }
      projectSettings.value = { ...settings.value, ...updated.settings }
      hint.value = 'Đã lưu tùy chọn. Các bước bị ảnh hưởng được đánh dấu cần cập nhật.'
    }
  })
}

function editSource(segment: Segment) {
  const job = currentJob.value
  const draft = sourceDrafts.value[segment.id]
  if (!job || !draft) return
  const blocked = sourceEditBlock(draft, job.metadata?.duration)
  if (blocked) { error.value = blocked; return }
  void perform('edit', async () => {
    const updated = await engine(settings.value, 'edit_source_segment', { job_id: job.id, segment_id: segment.id, ...draft, text: draft.text.trim() })
    storeJob(updated)
    const saved = updated.segments.find((item) => item.id === segment.id)
    if (saved) sourceDrafts.value[segment.id] = { text: saved.text, start: saved.start, end: saved.end }
    segmentAudio.value = null
  })
}

function importArtifact(id: 'extract' | 'transcribe' | 'translate' | 'synthesize', segmentId?: string) {
  const job = currentJob.value
  if (!job || editorDirty.value || sourceDirty.value) return
  void perform('project', async () => {
    const kind = id === 'extract' || id === 'synthesize' ? 'audio' : id === 'transcribe' ? 'transcript' : 'translation'
    const path = await pickArtifact(kind)
    if (!path) return
    storeJob(await engine(settings.value, 'import_artifact', { job_id: job.id, stage: id, path, ...(segmentId ? { segment_id: segmentId } : {}) }))
    resultMedia.value = null
    segmentAudio.value = null
    hint.value = 'Đã nhập và kiểm tra tệp. Xem kết quả trước khi chạy bước tiếp theo.'
  })
}

function selectStage(id: StageId) {
  selectedStage.value = id
  resultMedia.value = null
}

async function previewArtifact(path: string) {
  try {
    resultMedia.value = { path, url: await mediaUrl(path), video: /\.(mp4|mkv|mov|webm)$/i.test(path) }
  } catch (cause) { error.value = errorMessage(cause) }
}

type ProjectAction = 'open_project_folder' | 'open_project_output' | 'forget_project' | 'delete_project' | 'clean_project'
function manageProject(action: ProjectAction, confirmed = false, target: Job | null = currentJob.value) {
  if (busy.value) return
  const job = action === 'delete_project' && confirmed ? deleteConfirm.value : target
  if (!job) return
  if (action === 'delete_project' && !confirmed) { deleteConfirm.value = job; return }
  deleteConfirm.value = null
  void perform('project', async () => {
    await engine(settings.value, action, { job_id: job.id })
    if (action === 'forget_project' || action === 'delete_project') {
      jobs.value = jobs.value.filter((item) => item.id !== job.id)
      if (action === 'delete_project') {
        pendingByJob.delete(job.id)
        pendingSourceByJob.delete(job.id)
      } else if (currentJob.value?.id === job.id) rememberDrafts(job)
      if (currentJob.value?.id === job.id) {
        currentJob.value = null
        source.value = ''
        processingSource.value = ''
        metadata.value = null
        sourceUrl.value = ''
        resultMedia.value = null
        segmentAudio.value = null
        voiceUrl.value = ''
        drafts.value = {}
        sourceDrafts.value = {}
      }
      hint.value = action === 'delete_project' ? `Đã đưa “${job.title || basename(job.source)}” vào Thùng rác.` : 'Đã gỡ dự án khỏi danh sách. Thư mục vẫn được giữ.'
    } else if (action === 'clean_project') hint.value = 'Đã dọn tệp tạm của dự án.'
  })
}

function migrateProject() {
  const job = currentJob.value
  if (!job) return
  void perform('project', async () => {
    showJob(await engine(settings.value, 'migrate_project', { job_id: job.id }))
    hint.value = 'Đã sao chép dữ liệu sang thư mục dự án. Dữ liệu cũ được giữ nguyên.'
  })
}

function duplicateProject() {
  void perform('select', async () => {
    const path = await pickVideo()
    if (!path) return
    showJob(await engine(settings.value, 'create_project', { source: path, options: { ...options.value }, copy_source: copySource.value, new_project: true }))
    navigate('processing')
  })
}

function openProject() {
  void perform('project', async () => {
    const path = await pickDirectory()
    if (!path) return
    showJob(await engine(settings.value, 'open_project', { path }))
    navigate('processing')
    try { sourceUrl.value = await mediaUrl(source.value); videoError.value = false } catch { sourceUrl.value = ''; videoError.value = true }
  })
}

function relinkSource() {
  const job = currentJob.value
  if (!job) return
  void perform('project', async () => {
    const path = await pickVideo()
    if (!path) return
    showJob(await engine(settings.value, 'relink_source', { job_id: job.id, source: path }))
    sourceUrl.value = await mediaUrl(path)
    videoError.value = false
    hint.value = 'Đã xác minh và liên kết lại đúng video nguồn.'
  })
}

function editSegment(action: 'save' | 'translate' | 'regenerate', segment: Segment) {
  const job = currentJob.value
  if (!job || (action !== 'save' && !ready.value)) return
  const text = drafts.value[segment.id]?.trim() || ''
  if (action !== 'translate' && !text) return
  const args = { job_id: job.id, segment_id: segment.id }
  void perform('edit', async () => {
    if (segmentAudio.value?.id === segment.id) segmentAudio.value = null
    if (action !== 'translate' && text !== segment.translation) {
      const saved = await engine(settings.value, 'edit_segment', { ...args, text })
      storeJob(saved)
      drafts.value[segment.id] = saved.segments.find((item) => item.id === segment.id)?.translation ?? text
    }
    if (action !== 'save') {
      const updated = action === 'translate'
        ? await engine(settings.value, 'retranslate_segment', args)
        : await engine(settings.value, 'regenerate_segment', args)
      storeJob(updated)
      drafts.value[segment.id] = updated.segments.find((item) => item.id === segment.id)?.translation ?? text
    }
  })
}

async function previewSegment(segment: Segment) {
  const path = segment.audio_path
  if (!path) return
  try {
    segmentAudio.value = null
    segmentAudio.value = { id: segment.id, url: await mediaUrl(path) }
  } catch (cause) { error.value = errorMessage(cause) }
}

function exportJob() {
  const job = currentJob.value
  if (!job || !ready.value || editorDirty.value || sourceDirty.value || projectDirty.value) return
  void perform('export', async () => {
    progress.value = 0
    stage.value = ''
    processMessage.value = 'Đang chuẩn bị xuất video'
    processingSource.value = job.source
    logs.value = []
    cancelling.value = false
    canCancel.value = false
    pausing.value = false
    navigate('processing')
    try {
      const updated = await engine(settings.value, 'export', { job_id: job.id, verify_packets: settings.value.verify_packets })
      canCancel.value = false
      showJob(updated)
      if (updated.status === 'completed') navigate('editor')
    } catch (cause) {
      canCancel.value = false
      try {
        showJob(await engine(settings.value, 'get_job', { job_id: job.id }))
      } catch {
        // Report the export failure even if the checkpoint cannot be reopened.
      }
      throw cause
    } finally {
      cancelling.value = false
      pausing.value = false
    }
  })
}

function chooseDirectory(target: 'output_dir' | 'projects_dir' = 'output_dir') {
  void perform('directory', async () => {
    const path = await pickDirectory()
    if (path) settingsDraft.value[target] = path
  })
}

function saveSettings() {
  if (busy.value) return
  settings.value = { ...settingsDraft.value }
  localStorage.setItem(storageKey, JSON.stringify(settings.value))
  navigate('system')
  void checkSystem()
}

function onEngineEvent(payload: EngineEvent) {
  if (payload.type === 'project') {
    if (!currentJob.value || currentJob.value.id === payload.data.id) storeJob(payload.data)
    else {
      const index = jobs.value.findIndex((item) => item.id === payload.data.id)
      if (index >= 0) jobs.value[index] = mergeProjectUpdate(jobs.value[index]!, payload.data)
      else jobs.value.unshift(payload.data)
    }
    return
  }
  if (activity.value === 'install') {
    if (payload.type === 'progress') {
      installation.value.progress = Math.max(0, Math.min(100, payload.progress))
      installation.value.message = payload.message
      installation.value.canCancel = payload.progress < 100
    }
    installation.value.logs.push(payload.message)
    if (installation.value.logs.length > 500) installation.value.logs.shift()
    return
  }
  if (activity.value !== 'run' && activity.value !== 'export') return
  if (payload.type === 'progress') {
    canCancel.value = payload.progress < 100
    progress.value = Math.max(0, Math.min(100, payload.progress))
    stage.value = payload.stage
    processMessage.value = payload.message
  }
  if (payload.type === 'log' || payload.type === 'progress') {
    logs.value.push(payload.message)
    if (logs.value.length > 500) logs.value.shift()
  }
}

async function bind<T>(event: string, receive: (payload: T) => void) {
  const stop = await listen<T>(event, ({ payload }) => receive(payload))
  if (disposed) stop()
  else unlisten.push(stop)
}

onMounted(async () => {
  if (isTauri()) {
    try {
      await Promise.all([
        bind<EngineEvent>('engine-event', onEngineEvent),
        bind<string>('video-dropped', (path) => {
          if (busy.value) hint.value = 'Chờ tác vụ hiện tại hoàn tất trước khi đổi video.'
          else void perform('probe', () => inspectSource(path))
        }),
        bind<string>('video-drop-error', (message) => { error.value = message }),
      ])
    } catch (cause) {
      error.value = errorMessage(cause)
      return
    }
  }
  if (disposed) return
  const returning = localStorage.getItem('dubbing.seenSystemCheck') === 'yes'
  await checkSystem(returning)
})

onBeforeUnmount(() => {
  disposed = true
  unlisten.forEach((stop) => stop())
})
</script>

<template>
    <div class="studio">
    <aside class="rail">
      <div class="brand">
        <div class="brand-mark"><StudioIcon name="wave" :size="25" /></div>
        <div><strong>AI Video Dubbing</strong><span>Studio lồng tiếng</span></div>
      </div>
      <nav aria-label="Khu vực làm việc">
        <button v-for="page in pages" :key="page.id" type="button" :aria-current="tab === page.id ? 'page' : undefined"
          @click="navigate(page.id)">
          <StudioIcon :name="page.icon" :size="19" /> <span>{{ page.label }}</span>
          <i v-if="page.id === 'processing' && (activity === 'run' || activity === 'export')" class="running-dot" aria-label="Đang chạy"></i>
        </button>
      </nav>
      <div class="rail-footer"><StudioIcon name="shield" :size="17" /> <span>Xử lý trên máy</span></div>
    </aside>

    <main class="workspace">
      <header class="workspace-header">
        <div>
          <h1>{{ currentPage.title }}</h1>
          <p>{{ currentPage.description }}</p>
        </div>
        <button type="button" class="chip system-chip" :class="{ good: ready, warn: !ready }" @click="navigate('system')">
          <span class="status-dot" aria-hidden="true"></span>
          {{ activity === 'install' ? 'Đang cài đặt môi trường' : ready ? 'Hệ thống sẵn sàng' : activity === 'check' ? 'Đang kiểm tra hệ thống' : 'Kiểm tra hệ thống' }}
        </button>
      </header>
      <div v-if="error || hint" :role="error ? 'alert' : 'status'" class="notice top-notice" :class="{ error: error, warn: !error }">
        <StudioIcon :name="error ? 'alert' : 'pulse'" :size="19" />
        <span>{{ error || hint }}</span>
        <button class="plain-icon" type="button" aria-label="Đóng thông báo" @click="error = ''; hint = ''"><StudioIcon name="close" :size="17" /></button>
      </div>

      <template v-if="tab === 'home'">
        <div class="source-project-bar form-grid">
          <label class="checkbox-field"><input v-model="copySource" type="checkbox" :disabled="busy" /><span>Sao chép video gốc vào dự án khi chọn video<span class="micro">Bỏ chọn để tham chiếu tệp gốc và tiết kiệm dung lượng.</span></span></label>
          <button v-if="currentJob" class="btn" @click="navigate('processing')"><StudioIcon name="folder" :size="17" /> Mở các bước của dự án</button>
        </div>
        <div class="split home-split">
          <VideoSource :filename="filename" :metadata="metadata" :video-url="sourceUrl" :video-error="videoError"
            :using-proxy="usingProxy" :busy="busy" :probing="activity === 'probe'" @pick="chooseVideo"
            @failed="videoError = true" @proxy="preparePreview" />
          <section class="panel options-panel">
            <div class="panel-title"><h2><StudioIcon name="wave" /> Tùy chọn lồng tiếng</h2></div>
            <fieldset class="form-grid" :disabled="busy">
              <label v-if="metadata?.audio_tracks.length">Track âm thanh dùng để nhận dạng
                <select v-model.number="options.audio_track"><option :value="undefined">Track âm thanh đầu tiên</option><option v-for="(track, index) in metadata.audio_tracks" :key="track.index" :value="track.index">Track {{ index + 1 }} · {{ track.codec || 'Âm thanh' }}{{ track.language && track.language !== 'und' ? ' · ' + track.language : '' }}</option></select>
              </label>
              <div class="grid grid-cols-2 gap-3 language-fields">
                <label>Ngôn ngữ nguồn
                  <select v-model="options.source_language">
                    <option v-for="[value, label] in languages" :key="value" :value="value">{{ label }}</option>
                  </select>
                </label>
                <label>Ngôn ngữ đích<input value="Tiếng Việt" readonly /></label>
              </div>
              <div>
                <label for="voice">Giọng đọc</label>
                <div class="input-row">
                  <select id="voice" v-model="options.voice" :disabled="!report?.voices.length" @change="voiceUrl = ''">
                    <option value="" disabled>{{ report?.voices.length ? 'Chọn giọng' : 'Chưa có giọng khả dụng' }}</option>
                    <option v-for="voice in report?.voices" :key="voice.id" :value="voice.id">{{ voice.label }}</option>
                  </select>
                  <button class="btn" type="button" :disabled="!ready || !options.voice" @click="previewVoice">
                    <StudioIcon name="volume" :size="17" /> {{ activity === 'voice' ? 'Đang tạo' : 'Nghe thử' }}
                  </button>
                </div>
              </div>
              <audio v-if="voiceUrl" :key="voiceUrl" :src="voiceUrl" controls autoplay aria-label="Mẫu giọng đọc"
                @error="error = 'Không phát được mẫu giọng đọc.'" />
              <label class="range-label">
                <span>Tốc độ đọc <output>{{ options.speed.toFixed(2) }}×</output></span>
                <input v-model.number="options.speed" type="range" min="0.85" max="1.20" step="0.05" @input="voiceUrl = ''" />
              </label>
              <details class="advanced">
                <summary>Văn phong và thuật ngữ</summary>
                <div class="form-grid mt-3">
                  <label>Văn phong <input v-model.trim="options.style" placeholder="Ví dụ: tự nhiên, ngắn gọn" maxlength="500" /></label>
                  <label>Thuật ngữ
                    <textarea v-model.trim="options.glossary" rows="3" placeholder="Mỗi dòng một thuật ngữ, ví dụ: rendering = kết xuất" maxlength="5000" />
                  </label>
                </div>
              </details>
              <details class="advanced">
                <summary>Mô hình và thiết bị của dự án</summary>
                <div class="form-grid mt-3">
                  <label>Mô hình dịch
                    <select v-model="projectSettings.ollama_model"><option value="qwen2.5:3b">Qwen 2.5 · 3B (mặc định)</option><option value="qwen2.5:7b">Qwen 2.5 · 7B</option><option v-if="!['qwen2.5:3b', 'qwen2.5:7b'].includes(projectSettings.ollama_model)" :value="projectSettings.ollama_model">{{ projectSettings.ollama_model }}</option></select>
                  </label>
                  <div class="device-fields"><label v-for="field in deviceFields" :key="field.key">{{ field.label }}<select v-model="projectSettings[field.key]"><option value="auto">Tự động</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label></div>
                </div>
              </details>
            </fieldset>
            <div class="launch">
              <button v-if="currentJob && projectDirty" class="btn" type="button" :disabled="busy" @click="updateProject()"><StudioIcon name="check" :size="17" /> Lưu tùy chọn dự án</button>
              <button class="btn primary" type="button" :disabled="busy || !!startBlock || !currentJob || projectDirty || editorDirty || sourceDirty" @click="processVideo()">
                <StudioIcon name="play" :size="18" /> {{ mode === 'manual' ? 'Chạy bước đã chọn' : 'Chạy tự động' }}
              </button>
              <p class="micro">{{ projectDirty ? 'Lưu tùy chọn trước khi xử lý.' : startBlock || 'Xem các bước để chọn chế độ từng bước hoặc dừng sau một kết quả.' }}</p>
            </div>
          </section>
        </div>
        <div class="studio-note"><StudioIcon name="shield" :size="17" />
          <span>Video nguồn được giữ nguyên. Ứng dụng tạo một tệp riêng và sao chép luồng hình khi xuất.</span>
        </div>
      </template>

      <ProcessMonitor v-else-if="tab === 'processing'" :job="currentJob" :jobs="jobs" :filename="basename(processingSource)"
        :progress="progress" :stage="stage" :message="processMessage" :logs="logs" :active="activity === 'run' || activity === 'export'"
        :busy="busy" :cancelling="cancelling" :can-cancel="canCancel" :ready="ready" @cancel="cancel" @retry="processVideo(true)"
        :pausing="pausing" :selected-stage="selectedStage" :mode="mode" :stop-after="stopAfter" :dirty="editorDirty || sourceDirty || projectDirty" :media="resultMedia"
        @open="openJob" @editor="navigate('editor')" @home="navigate('home')" @refresh="refreshHistory" @pause="pause"
        @select-stage="selectStage" @run-stage="runStage" @mode="updateProject" @stop-after="stopAfter = $event"
        @import-artifact="importArtifact" @preview="previewArtifact" @manage="manageProject" @delete-project="manageProject('delete_project', false, $event)" @migrate="migrateProject" @duplicate="duplicateProject"
        @open-project="openProject" @relink="relinkSource"
        @media-error="error = 'Không phát được tệp kết quả. Kiểm tra tệp trong thư mục dự án.'" />

      <TranscriptEditor v-else-if="tab === 'editor'" v-model:drafts="drafts" v-model:source-drafts="sourceDrafts" :job="currentJob" :busy="busy" :ready="ready" :dirty="editorDirty || sourceDirty || projectDirty"
        :audio="segmentAudio" @save="editSegment('save', $event)" @translate="editSegment('translate', $event)"
        @regenerate="editSegment('regenerate', $event)" @preview="previewSegment" @export="exportJob"
        @save-source="editSource" @import-audio="importArtifact('synthesize', $event.id)" @pause="pause" :pausing="pausing"
        @home="navigate('home')" @history="navigate('processing')" @audio-error="error = 'Không phát được âm thanh của đoạn này.'" />

      <SettingsForm v-else-if="tab === 'settings'" v-model="settingsDraft" :busy="busy" :dirty="settingsDirty"
        @choose-directory="chooseDirectory" @save="saveSettings" />

      <SystemCheck v-else :report="report" :busy="busy" :checking="activity === 'check'" :installation="installation"
        :whisper-model="settings.whisper_model" :ollama-model="settings.ollama_model" @check="checkSystem()"
        @install="installDependencies" @cancel="cancelInstallation" @settings="navigate('settings')" @home="navigate('home')" />
    </main>
    <div v-if="deleteConfirm" class="dialog-backdrop" @keydown.esc="deleteConfirm = null">
      <section class="panel project-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-heading" aria-describedby="delete-description">
        <h2 id="delete-heading">Đưa dự án vào Thùng rác?</h2>
        <p id="delete-description">Thư mục “{{ deleteConfirm.title || basename(deleteConfirm.source) }}”, lời thoại, âm thanh và mọi bản xuất bên trong sẽ được đưa vào Thùng rác. Video nguồn tham chiếu bên ngoài được giữ nguyên.</p>
        <p class="artifact-path">{{ deleteConfirm.directory }}</p>
        <div class="panel-actions"><button class="btn" autofocus @click="deleteConfirm = null">Giữ dự án</button><button class="btn danger" :disabled="busy" @click="manageProject('delete_project', true)">Đưa vào Thùng rác</button></div>
      </section>
    </div>
  </div>
</template>
