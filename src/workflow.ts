export interface Settings {
  python_path: string
  ffmpeg_path: string
  ffprobe_path: string
  ollama_model: string
  whisper_model: string
  tts_model_path: string
  hardware_mode: 'auto' | 'cpu' | 'gpu'
  output_dir: string
  verify_packets: boolean
  projects_dir?: string
  asr_device?: Device
  translation_device?: Device
  tts_device?: Device
}

export type Device = 'auto' | 'cpu' | 'gpu'
export type ProjectMode = 'auto' | 'manual'
export type StageId = 'probe' | 'extract' | 'transcribe' | 'translate' | 'synthesize' | 'align' | 'mux'
export type StageStatus = 'pending' | 'running' | 'ready' | 'stale' | 'failed' | 'paused'
export interface StageRecord {
  status: StageStatus
  key?: string
  error?: string | null
  completed?: number
  total?: number
  duration_ms?: number
  manual?: boolean
}
export interface SourceDraft { text: string; start: number; end: number }

export interface Options {
  source_language: string
  voice: string
  speed: number
  style: string
  glossary: string
  audio_track?: number
}

export interface Segment {
  id: string
  start: number
  end: number
  text: string
  translation: string
  audio_path?: string | null
  warning?: string | null
  translation_stale?: boolean
  translation_needs_review?: boolean
}

export interface Job {
  id: string
  source: string
  status: 'running' | 'failed' | 'cancelled' | 'completed' | 'edited' | 'ready' | 'paused'
  stage: string
  error?: string | null
  output?: string | null
  segments: Segment[]
  warnings: string[]
  options: Options
  logs?: string[]
  directory?: string
  title?: string
  mode?: ProjectMode
  source_mode?: 'reference' | 'copy'
  source_available?: boolean
  metadata?: Metadata
  stages?: Partial<Record<StageId, StageRecord>>
  artifacts?: Record<string, string>
  exports?: { path: string; created: string | number; revision: string | number }[]
  settings?: Partial<Settings>
  legacy?: boolean
}

export interface Metadata {
  duration: number
  width: number
  height: number
  fps: number
  codec: string
  size: number
  audio_tracks: { index: number; codec: string | null; channels: number | null; language: string | null }[]
}

export type DependencyStatus = 'installed' | 'missing' | 'incompatible' | 'unchecked'

export interface DependencyCheck {
  id?: string
  name: string
  status: DependencyStatus
  detail: string
  required?: boolean
}

export interface CheckReport {
  ready: boolean
  checks: DependencyCheck[]
  hardware: {
    cpu?: string
    ram_gb?: number | null
    gpus: { name: string; vram_mb?: number | null }[]
    cuda_devices?: number | null
  }
  voices: { id: string; label: string }[]
  settings_patch?: Partial<Pick<Settings, 'python_path' | 'ffmpeg_path' | 'ffprobe_path'>>
  setup_log?: SetupLog
}

export interface SetupLog {
  path: string
  lines: string[]
}

export interface DependencyInstallation {
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  logs: string[]
  error: string
  cancelling: boolean
  canCancel: boolean
  logPath: string
}

export type EngineEvent =
  | { type: 'progress'; stage: string; progress: number; message: string }
  | { type: 'log'; level: string; message: string }
  | { type: 'project'; data: Job }

export const defaultSettings: Settings = {
  python_path: '',
  ffmpeg_path: '',
  ffprobe_path: '',
  ollama_model: 'qwen2.5:3b',
  whisper_model: 'small',
  tts_model_path: '',
  hardware_mode: 'auto',
  output_dir: '',
  verify_packets: false,
  projects_dir: '',
  asr_device: 'auto',
  translation_device: 'auto',
  tts_device: 'auto',
}

export const stages: { id: StageId; label: string; result: string }[] = [
  { id: 'probe', label: 'Đọc video', result: 'Thông tin nguồn và track âm thanh' },
  { id: 'extract', label: 'Tách âm thanh', result: 'Âm thanh gốc' },
  { id: 'transcribe', label: 'Nhận dạng', result: 'Lời gốc và mốc thời gian' },
  { id: 'translate', label: 'Dịch thoại', result: 'Bản dịch tiếng Việt' },
  { id: 'synthesize', label: 'Tạo giọng', result: 'Lời đọc từng đoạn' },
  { id: 'align', label: 'Đồng bộ', result: 'Lời đọc theo timeline' },
  { id: 'mux', label: 'Xuất video', result: 'Các phiên bản video đã xuất' },
]

export const stageStatusLabels: Record<StageStatus, string> = {
  pending: 'Chưa chạy', running: 'Đang chạy', ready: 'Đã có kết quả', stale: 'Cần cập nhật', failed: 'Gặp lỗi', paused: 'Tạm dừng',
}
export const jobStatusLabels: Record<Job['status'], string> = {
  running: 'Gián đoạn', completed: 'Đã xuất', failed: 'Gặp lỗi', cancelled: 'Đã hủy', edited: 'Đã chỉnh sửa', ready: 'Sẵn sàng', paused: 'Tạm dừng',
}

export function migrateSettings(saved: Partial<Settings>, version = 1): Settings {
  const result = { ...defaultSettings, ...saved }
  for (const key of ['asr_device', 'translation_device', 'tts_device'] as const) {
    if (saved[key] === undefined) result[key] = saved.hardware_mode ?? defaultSettings.hardware_mode
  }
  if (version < 2) {
    const path = result.python_path.replace(/\//g, '\\')
    if (/\\AppData\\Local\\com\.aivideodubbing\.desktop\\(?:venv\\Scripts|runtime\\python311)\\python\.exe$/i.test(path)) result.python_path = ''
  }
  return result
}

export function stageBlock(job: Job | null, stage: StageId, force = false) {
  if (!job) return 'Chọn video để tạo hoặc mở dự án.'
  if (!force && job.stages?.[stage]?.status === 'ready') return ''
  const dependencies: Record<StageId, StageId[]> = {
    probe: [], extract: ['probe'], transcribe: ['extract'], translate: ['transcribe'],
    synthesize: ['translate'], align: ['probe', 'synthesize'], mux: ['probe', 'align'],
  }
  for (const prerequisite of dependencies[stage]) {
    if (job.stages?.[prerequisite]?.status !== 'ready') return `Hoàn tất bước ${stages.find((item) => item.id === prerequisite)!.label} trước.`
  }
  return ''
}

export function mergeProjectUpdate(current: Job, update: Partial<Job> & Pick<Job, 'id'>): Job {
  if (current.id !== update.id) return update as Job
  return {
    ...current, ...update,
    stages: { ...current.stages, ...update.stages },
    artifacts: { ...current.artifacts, ...update.artifacts },
  }
}

export function reconcileDrafts(previous: Segment[], incoming: Segment[], drafts: Record<string, string>) {
  const oldText = new Map(previous.map((segment) => [segment.id, segment.translation]))
  return Object.fromEntries(incoming.map((segment) => [
    segment.id, hasDraftChange(drafts[segment.id], oldText.get(segment.id) ?? segment.translation) ? drafts[segment.id]! : segment.translation,
  ]))
}

export function sourceEditBlock(draft: SourceDraft, duration?: number) {
  if (!draft.text.trim()) return 'Lời gốc không được để trống.'
  if (!Number.isFinite(draft.start) || !Number.isFinite(draft.end) || draft.start < 0 || draft.end <= draft.start) return 'Mốc thời gian cần hợp lệ và kết thúc sau bắt đầu.'
  if (duration && draft.end > duration) return 'Mốc kết thúc vượt thời lượng video.'
  return ''
}

export function processingBlock(report: CheckReport | null, metadata: Metadata | null, voice: string) {
  if (!report?.ready) return 'Kiểm tra hệ thống trước khi bắt đầu.'
  if (!metadata) return 'Chọn một video có lời thoại.'
  if (!metadata.audio_tracks.length) return 'Video này không có track âm thanh.'
  if (!report.voices.some((preset) => preset.id === voice)) return 'Chọn giọng đọc để tiếp tục.'
  return ''
}

export function hasDraftChange(draft: string | undefined, translation: string) {
  return draft !== undefined && draft.trim() !== translation
}

export function dependencySummary(checks: Pick<DependencyCheck, 'status'>[]) {
  const summary = { installed: 0, missing: 0, incompatible: 0, unchecked: 0 }
  for (const check of checks) summary[check.status] += 1
  return summary
}

export function applyDependencySettings(settings: Settings, patch: CheckReport['settings_patch']) {
  const updated = { ...settings }
  for (const key of ['python_path', 'ffmpeg_path', 'ffprobe_path'] as const) {
    const path = patch?.[key]
    if (typeof path === 'string' && path.trim()) updated[key] = path.trim()
  }
  return updated
}

export function recordSetupError(current: DependencyInstallation, message: string, cancelled: boolean): DependencyInstallation {
  const logs = [...current.logs]
  if (logs.at(-1) !== message) logs.push(message)
  return {
    ...current, status: cancelled ? 'cancelled' : 'failed', error: message,
    message: cancelled ? 'Đã dừng cài đặt.' : 'Cài đặt chưa hoàn tất.', canCancel: false, logs: logs.slice(-500),
  }
}

export function mergeSetupLog(current: DependencyInstallation, saved: SetupLog | undefined, ready: boolean): DependencyInstallation {
  if (!saved) return current
  const messages: string[] = []
  let outcome: 'failed' | 'cancelled' | 'completed' | undefined
  let lastError = ''
  let progress = current.progress
  for (const line of saved.lines) {
    if (!line.trim()) continue
    let message = line
    try {
      const event = JSON.parse(line) as { type?: string; message?: string; progress?: number; data?: { ready?: boolean } }
      message = typeof event.message === 'string' ? event.message : ''
      if (event.type === 'progress' && typeof event.progress === 'number') progress = Math.max(0, Math.min(100, event.progress))
      if (event.type === 'error') {
        lastError = message || 'Thiết lập đã dừng do lỗi. Xem nhật ký bên dưới.'
        outcome = /đã hủy|đã dừng|cancelled/i.test(lastError) ? 'cancelled' : 'failed'
      } else if (event.type === 'result' && typeof event.data?.ready === 'boolean') {
        outcome = event.data.ready ? 'completed' : 'failed'
        lastError = event.data.ready ? '' : message || 'Môi trường chưa sẵn sàng. Xem các thành phần chưa đạt bên dưới.'
      }
    } catch { /* Native stderr and vendor output can also be plain text. */ }
    message = message.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\r/g, '')
    if (message && messages.at(-1) !== message) messages.push(message)
  }
  const result = { ...current, logPath: saved.path }
  if (messages.length) result.logs = messages.slice(-500)
  if (ready && current.status !== 'running') {
    result.status = 'completed'
    result.progress = 100
    result.error = ''
    result.message = 'Thiết lập hoàn tất. Hệ thống sẵn sàng lồng tiếng.'
  } else if (current.status === 'idle') {
    if (!outcome && messages.length) {
      outcome = 'failed'
      lastError = 'Lần thiết lập trước đã dừng trước khi hoàn tất. Bấm Tiếp tục thiết lập để chạy lại.'
    }
    result.progress = ready || outcome === 'completed' ? 100 : progress
    result.status = ready ? 'completed' : outcome || 'idle'
    result.error = ready ? '' : lastError
    result.message = ready || outcome === 'completed'
      ? 'Thiết lập hoàn tất.'
      : lastError ? 'Lần thiết lập trước chưa hoàn tất.' : 'Nhật ký thiết lập gần nhất.'
  }
  if (!ready && result.status === 'completed') {
    result.status = 'failed'
    result.progress = 0
    result.error = 'Môi trường hiện chưa sẵn sàng. Bấm Tiếp tục thiết lập để bổ sung các thành phần còn thiếu.'
    result.message = 'Cần kiểm tra lại môi trường.'
  }
  if (!ready && !result.error && lastError) result.error = lastError
  if (result.error && result.logs.at(-1) !== result.error) result.logs = [...result.logs, result.error].slice(-500)
  return result
}

export function timestamp(seconds: number, precise = true) {
  const milliseconds = Math.max(0, Math.round(seconds * 1000))
  const time = Math.floor(milliseconds / 1000)
  const hours = Math.floor(time / 3600)
  const minutes = Math.floor(time / 60) % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${hours ? pad(hours) + ':' : ''}${pad(minutes)}:${pad(time % 60)}${precise ? '.' + String(milliseconds % 1000).padStart(3, '0') : ''}`
}
