<script setup lang="ts">
import { computed } from 'vue'
import StudioIcon from './StudioIcon.vue'
import { jobStatusLabels, stageBlock, stages, stageStatusLabels, timestamp } from '../workflow'
import type { Job, ProjectMode, StageId } from '../workflow'

const props = defineProps<{
  job: Job | null; jobs: Job[]; filename: string; progress: number; stage: string; message: string; logs: string[]
  active: boolean; busy: boolean; cancelling: boolean; canCancel: boolean; ready: boolean; pausing: boolean
  selectedStage: StageId; mode: ProjectMode; stopAfter: StageId | ''; dirty: boolean
  media: { path: string; url: string; video: boolean } | null
}>()
defineEmits<{
  cancel: []; pause: []; retry: []; open: [job: Job]; editor: []; home: []; refresh: []
  selectStage: [id: StageId]; runStage: [id: StageId, force?: boolean]; mode: [mode: ProjectMode]; stopAfter: [stage: StageId | '']
  importArtifact: [stage: 'extract' | 'transcribe' | 'translate']; preview: [path: string]
  manage: [action: 'open_project_folder' | 'open_project_output' | 'forget_project' | 'delete_project' | 'clean_project']
  migrate: []; duplicate: []; mediaError: []; openProject: []; relink: []; deleteProject: [job: Job]
}>()

const selected = computed(() => stages.find((item) => item.id === props.selectedStage)!)
const record = computed(() => props.job?.stages?.[props.selectedStage])
const block = computed(() => stageBlock(props.job, props.selectedStage))
const forceBlock = computed(() => stageBlock(props.job, props.selectedStage, true))
const artifact = computed(() => {
  const key = ({ extract: 'source_audio', transcribe: 'transcript', translate: 'translation', align: 'dubbed' } as Record<string, string>)[props.selectedStage]
  return key ? props.job?.artifacts?.[key] : undefined
})
const count = computed(() => {
  if (!props.job) return ''
  if (record.value?.total) return `${record.value.completed ?? 0} / ${record.value.total} đoạn`
  if (props.selectedStage === 'translate') return `${props.job.segments.filter((item) => item.translation?.trim()).length} / ${props.job.segments.length} đoạn đã dịch`
  if (props.selectedStage === 'synthesize') return `${props.job.segments.filter((item) => item.audio_path).length} / ${props.job.segments.length} đoạn có giọng`
  return props.job.segments.length ? `${props.job.segments.length} đoạn thoại` : ''
})
const importStage = computed(() => ['extract', 'transcribe', 'translate'].includes(props.selectedStage) ? props.selectedStage as 'extract' | 'transcribe' | 'translate' : null)
const basename = (path: string) => path.split(/[\\/]/).at(-1)
const date = (value: string | number) => {
  const parsed = new Date(typeof value === 'number' && value < 100000000000 ? value * 1000 : value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('vi-VN')
}
</script>

<template>
  <div class="split process-split">
    <section class="panel process-panel" aria-labelledby="progress-heading">
      <div class="panel-title flex items-center justify-between gap-3">
        <h2 id="progress-heading"><StudioIcon name="activity" /> {{ job?.title || filename || 'Dự án đang mở' }}</h2>
        <span v-if="active" class="chip active">{{ pausing ? 'Chờ tạm dừng' : 'Đang chạy' }}</span>
        <span v-else-if="job" class="chip" :class="job.status === 'completed' ? 'good' : ''">{{ jobStatusLabels[job.status] }}</span>
      </div>
      <template v-if="job">
        <p class="project-directory" :title="job.directory">{{ job.directory || job.source }}</p>
        <div v-if="job.source_available === false" class="notice warn"><StudioIcon name="alert" :size="18" /><div><p>Không tìm thấy video nguồn. Chọn lại đúng video để tiếp tục xử lý.</p><button class="btn small" :disabled="busy" @click="$emit('relink')">Chọn lại video gốc</button></div></div>
        <div v-if="job.legacy" class="notice warn legacy-project">
          <StudioIcon name="folder" :size="18" />
          <div><p>Dữ liệu từ phiên bản cũ. Sao chép sang thư mục dự án để xử lý từng bước.</p>
            <button class="btn small" :disabled="busy" @click="$emit('migrate')">Chuyển thành dự án</button>
          </div>
        </div>
        <div class="workflow-controls form-grid">
          <label>Chế độ
            <select :value="mode" :disabled="busy || job.legacy" @change="$emit('mode', ($event.target as HTMLSelectElement).value as ProjectMode)">
              <option value="auto">Tự động</option><option value="manual">Từng bước</option>
            </select>
          </label>
          <label v-if="mode === 'auto'">Dừng sau
            <select :value="stopAfter" :disabled="busy" @change="$emit('stopAfter', ($event.target as HTMLSelectElement).value as StageId | '')">
              <option value="">Xuất video hoàn tất</option>
              <option v-for="item in stages.slice(0, -1)" :key="item.id" :value="item.id">{{ item.label }}</option>
            </select>
          </label>
          <button v-if="!active && mode === 'auto'" class="btn primary" :disabled="busy || !ready || dirty || job.legacy" @click="$emit('retry')">
            <StudioIcon name="play" :size="17" /> {{ job.status === 'paused' ? 'Tiếp tục tự động' : 'Chạy tự động' }}
          </button>
        </div>
        <div v-if="active" class="live-progress" aria-live="polite">
          <div class="progress-label"><p>{{ message || 'Đang xử lý' }}</p><strong>{{ Math.round(progress) }}%</strong></div>
          <progress :value="progress" max="100" aria-label="Tiến độ xử lý video" />
          <div class="panel-actions">
            <button class="btn" :disabled="pausing || cancelling" @click="$emit('pause')"><StudioIcon name="stop" :size="16" /> {{ pausing ? 'Đang chờ lưu và tạm dừng' : 'Tạm dừng sau nhóm hiện tại' }}</button>
            <button class="btn danger small" :disabled="cancelling || !canCancel" @click="$emit('cancel')">{{ cancelling ? 'Đang hủy' : 'Hủy ngay' }}</button>
          </div>
          <p v-if="pausing" class="micro">Yêu cầu model hiện tại sẽ hoàn tất và lưu kết quả trước khi dừng.</p>
        </div>
        <ol class="workflow-stages" aria-label="Các bước xử lý">
          <li v-for="(item, index) in stages" :key="item.id">
            <button :aria-current="selectedStage === item.id ? 'step' : undefined" :class="job.stages?.[item.id]?.status" @click="$emit('selectStage', item.id)">
              <span class="step-number">{{ index + 1 }}</span><span>{{ item.label }}<small>{{ stageStatusLabels[job.stages?.[item.id]?.status || 'pending'] }}</small></span>
            </button>
          </li>
        </ol>
        <section class="stage-result" :aria-label="selected.label + ': kết quả'">
          <div class="stage-result-heading"><div><h3>{{ selected.label }}</h3><p class="micro">{{ selected.result }}</p></div>
            <span v-if="count" class="chip neutral" aria-live="polite">{{ count }}</span>
          </div>
          <p v-if="record?.status === 'stale'" class="notice warn">Kết quả đã lưu vẫn xem được. Chạy lại bước này để dùng với thay đổi mới.</p>
          <p v-if="record?.error" class="notice error">{{ record.error }}</p>
          <template v-if="selectedStage === 'probe'">
            <dl v-if="job.metadata" class="metadata">
              <div><dt>Thời lượng</dt><dd>{{ timestamp(job.metadata.duration, false) }}</dd></div>
              <div><dt>Kích thước</dt><dd>{{ job.metadata.width }} × {{ job.metadata.height }}</dd></div>
              <div><dt>Track âm thanh</dt><dd>{{ job.metadata.audio_tracks.length }}</dd></div>
              <div><dt>Video nguồn</dt><dd>{{ job.source_mode === 'copy' ? 'Bản sao trong dự án' : 'Tham chiếu tệp gốc' }}</dd></div>
            </dl>
            <div class="panel-actions"><button class="btn small" @click="$emit('home')">Xem video và tùy chọn</button></div>
          </template>
          <template v-else-if="selectedStage === 'transcribe' || selectedStage === 'translate' || selectedStage === 'synthesize'">
            <div v-if="job.segments.length" class="result-cues">
              <div v-for="segment in job.segments.slice(0, 3)" :key="segment.id">
                <time>{{ timestamp(segment.start) }}</time>
                <p>{{ selectedStage === 'transcribe' ? segment.text : segment.translation || 'Đang chờ bản dịch…' }}</p>
              </div>
              <button class="btn small" @click="$emit('editor')">Xem và sửa {{ job.segments.length }} đoạn thoại</button>
            </div>
            <p v-else class="empty-copy">Chạy bước này hoặc nhập kết quả để xem lời thoại.</p>
          </template>
          <template v-if="artifact">
            <p class="artifact-path">{{ artifact }}</p>
            <button v-if="selectedStage === 'extract' || selectedStage === 'align'" class="btn small" @click="$emit('preview', artifact)"><StudioIcon name="volume" :size="16" /> Nghe kết quả</button>
          </template>
          <p v-else-if="selectedStage === 'extract' || selectedStage === 'align'" class="empty-copy">Chưa có âm thanh cho bước này.</p>
          <template v-if="selectedStage === 'mux'">
            <ol v-if="job.exports?.length" class="export-history" aria-label="Lịch sử xuất video">
              <li v-for="(item, index) in [...job.exports].reverse()" :key="item.path">
                <div><strong>Bản xuất {{ job.exports.length - index }}</strong><small>{{ date(item.created) }} · Phiên sửa {{ item.revision }}</small><p>{{ item.path }}</p></div>
                <button class="btn small" @click="$emit('preview', item.path)"><StudioIcon name="play" :size="16" /> Xem</button>
              </li>
            </ol>
            <p v-else class="empty-copy">Chưa có phiên bản video đã xuất.</p>
          </template>
          <template v-if="media">
            <video v-if="media.video" :key="media.url" class="result-video" :src="media.url" controls @error="$emit('mediaError')" />
            <audio v-else :key="media.url" :src="media.url" controls autoplay aria-label="Âm thanh kết quả bước" @error="$emit('mediaError')" />
          </template>
          <p v-if="record?.duration_ms !== undefined" class="micro stage-duration">Thời gian bước này: {{ (record.duration_ms / 1000).toFixed(1) }} giây</p>
          <p v-if="dirty" class="micro warn-text">Lưu lời thoại và tùy chọn đã sửa trước khi chạy hoặc nhập kết quả.</p>
          <p v-else-if="block && !active" class="micro">{{ block }}</p>
          <div class="panel-actions stage-actions">
            <button v-if="importStage" class="btn small" :disabled="busy || dirty || job.legacy" @click="$emit('importArtifact', importStage)"><StudioIcon name="folder" :size="16" /> Nhập {{ selectedStage === 'extract' ? 'WAV' : 'JSON / SRT' }}</button>
            <button class="btn" :title="forceBlock" :disabled="busy || !ready || !!forceBlock || dirty || job.legacy" @click="$emit('runStage', selectedStage, true)"><StudioIcon name="refresh" :size="16" /> Chạy lại bước</button>
            <button class="btn primary" :disabled="busy || !ready || !!block || dirty || job.legacy" @click="$emit('runStage', selectedStage)"><StudioIcon name="play" :size="16" /> Chạy bước này</button>
          </div>
        </section>
        <p v-if="job.error && !active" class="notice error"><StudioIcon name="alert" :size="18" /> {{ job.error }}</p>
        <details class="project-management">
          <summary>Thư mục và dữ liệu dự án</summary>
          <div class="panel-actions">
            <button class="btn small" :disabled="busy" @click="$emit('manage', 'open_project_folder')"><StudioIcon name="folder" :size="16" /> Mở thư mục</button>
            <button class="btn small" :disabled="busy || !job.output" @click="$emit('manage', 'open_project_output')">Mở video đầu ra</button>
            <button class="btn small" :disabled="busy" @click="$emit('duplicate')">Tạo dự án riêng từ video</button>
            <button class="btn small" :disabled="busy" @click="$emit('relink')">Chọn lại video gốc</button>
            <button class="btn small" :disabled="busy || job.legacy" @click="$emit('manage', 'clean_project')">Dọn tệp tạm</button>
            <button class="btn small" :disabled="busy" @click="$emit('manage', 'forget_project')">Gỡ khỏi danh sách</button>
            <button class="btn small danger" :disabled="busy" @click="$emit('manage', 'delete_project')">Đưa dự án vào Thùng rác</button>
          </div>
          <p class="micro">Gỡ khỏi danh sách giữ nguyên thư mục. Dọn tệp tạm giữ kết quả và các bản xuất.</p>
        </details>
        <details class="installation-logs"><summary>Nhật ký xử lý ({{ logs.length }} dòng)</summary><pre class="job-log" tabindex="0">{{ logs.length ? logs.join('\n') : 'Chưa có nhật ký.' }}</pre></details>
      </template>
      <div v-else class="empty-panel"><StudioIcon name="folder" :size="34" /><h3>Chọn video để mở dự án</h3><p>Mỗi video có thư mục riêng chứa lời thoại, âm thanh và các bản xuất.</p><button class="btn primary" @click="$emit('home')">Chọn video</button></div>
    </section>
    <section class="panel history-panel" aria-labelledby="history-heading">
      <div class="panel-title flex items-center justify-between gap-3"><h2 id="history-heading"><StudioIcon name="clock" /> Dự án đã lưu</h2>
        <button class="btn icon-btn" aria-label="Tải lại danh sách dự án" :disabled="busy" @click="$emit('refresh')"><StudioIcon name="refresh" :size="17" /></button>
      </div>
      <button class="btn small open-project" :disabled="busy" @click="$emit('openProject')"><StudioIcon name="folder" :size="16" /> Mở thư mục dự án</button>
      <p v-if="!jobs.length" class="empty-copy">Chọn video tại bàn dựng để tạo dự án đầu tiên.</p>
      <ul v-else class="history-list">
        <li v-for="item in jobs" :key="item.id" class="history-row">
          <button class="history-item" :disabled="busy" :aria-pressed="item.id === job?.id" @click="$emit('open', item)">
            <StudioIcon name="film" :size="19" /><span class="history-name"><strong :title="item.source">{{ item.title || basename(item.source) }}</strong><small>{{ jobStatusLabels[item.status] }}<template v-if="item.legacy"> · Dữ liệu cũ</template></small></span><StudioIcon name="arrow" :size="16" />
          </button>
          <button class="btn small danger history-delete" :disabled="busy" :aria-label="'Xóa dự án ' + (item.title || basename(item.source))" @click="$emit('deleteProject', item)">Xóa</button>
        </li>
      </ul>
    </section>
  </div>
</template>
