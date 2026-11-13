<script setup lang="ts">
import StudioIcon from './StudioIcon.vue'
import { computed, ref, watch } from 'vue'
import { hasDraftChange, sourceEditBlock, timestamp } from '../workflow'
import type { Job, Segment, SourceDraft } from '../workflow'

const props = defineProps<{
  job: Job | null
  busy: boolean
  ready: boolean
  dirty: boolean
  audio: { id: string; url: string } | null
  pausing: boolean
}>()

const drafts = defineModel<Record<string, string>>('drafts', { required: true })
const sourceDrafts = defineModel<Record<string, SourceDraft>>('sourceDrafts', { required: true })
const page = ref(1)
const pageSize = 30
const pageCount = computed(() => Math.max(1, Math.ceil((props.job?.segments.length ?? 0) / pageSize)))
const visibleSegments = computed(() => props.job?.segments.slice((page.value - 1) * pageSize, page.value * pageSize) ?? [])
function turnPage(direction: number) {
  page.value = Math.max(1, Math.min(pageCount.value, page.value + direction))
  window.scrollTo({ top: 0 })
}
watch(() => props.job?.id, () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })
const sourceChanged = (segment: Segment) => {
  const draft = sourceDrafts.value[segment.id]
  return !!draft && (draft.text !== segment.text || draft.start !== segment.start || draft.end !== segment.end)
}
defineEmits<{
  save: [segment: Segment]
  translate: [segment: Segment]
  regenerate: [segment: Segment]
  preview: [segment: Segment]
  export: []
  home: []
  history: []
  audioError: []
  saveSource: [segment: Segment]
  importAudio: [segment: Segment]
  pause: []
}>()
</script>

<template>
  <div v-if="!job?.segments.length" class="panel empty-panel">
    <StudioIcon name="lines" :size="34" />
    <h2>Chưa có đoạn thoại để chỉnh sửa</h2>
    <p>Mở dự án đã nhận dạng lời thoại hoặc xử lý video mới.</p>
    <div class="flex flex-wrap justify-center gap-2">
      <button class="btn" @click="$emit('history')"><StudioIcon name="clock" :size="16" /> Xem công việc</button>
      <button class="btn primary" @click="$emit('home')">Về bàn dựng <StudioIcon name="arrow" :size="17" /></button>
    </div>
  </div>
  <template v-else>
    <section class="panel editor-summary">
      <div class="min-w-0">
        <h2>{{ job.segments.length }} đoạn thoại</h2>
        <p v-if="dirty" class="micro warn-text">Lưu các đoạn và tùy chọn đã sửa trước khi xuất lại.</p>
        <p v-else class="micro">Sửa lời gốc, mốc thời gian và bản dịch trước khi tạo giọng.</p>
      </div>
      <button class="btn primary" :disabled="busy || !ready || dirty" @click="$emit('export')">
        <StudioIcon name="download" :size="18" /> {{ job.output ? 'Xuất lại video' : 'Xuất video' }}
      </button>
      <p v-if="job.output" class="output-path"><StudioIcon name="check" :size="17" /> <span>{{ job.output }}</span></p>
    </section>
    <div v-if="busy && job.status === 'running'" class="notice warn live-editor"><StudioIcon name="activity" :size="18" /><span>Kết quả đang được lưu theo nhóm. Tạm dừng để chỉnh sửa.</span><button class="btn small" :disabled="pausing" @click="$emit('pause')">{{ pausing ? 'Chờ lưu và tạm dừng' : 'Tạm dừng an toàn' }}</button></div>
    <details v-if="job.warnings.length" class="panel warning-list">
      <summary><StudioIcon name="alert" :size="18" /> {{ job.warnings.length }} cảnh báo đồng bộ</summary>
      <ul><li v-for="(warning, index) in job.warnings" :key="index">{{ warning }}</li></ul>
    </details>
    <nav class="transcript-pagination" aria-label="Phân trang lời thoại">
      <span class="micro">Đoạn {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, job.segments.length) }} / {{ job.segments.length }}</span>
      <div><button class="btn small" :disabled="page <= 1" @click="page--">Trước</button><label>Trang <input v-model.number="page" type="number" min="1" :max="pageCount" @change="page = Math.max(1, Math.min(pageCount, Math.floor(Number(page) || 1)))" /> / {{ pageCount }}</label><button class="btn small" :disabled="page >= pageCount" @click="page++">Sau</button></div>
    </nav>
    <ol class="segment-list" :start="(page - 1) * pageSize + 1" aria-label="Danh sách đoạn thoại">
      <li v-for="(segment, index) in visibleSegments" :key="segment.id" class="panel segment">
        <div class="segment-time">
          <span class="cue">{{ String((page - 1) * pageSize + index + 1).padStart(2, '0') }}</span>
          <StudioIcon name="clock" :size="16" />
          <time>{{ timestamp(segment.start) }}</time>
          <span aria-hidden="true">–</span>
          <time>{{ timestamp(segment.end) }}</time>
        </div>
        <div class="segment-text">
          <div v-if="sourceDrafts[segment.id]" class="original form-grid">
            <label>Lời gốc<textarea v-model="sourceDrafts[segment.id]!.text" :disabled="busy" maxlength="10000" rows="3" /></label>
            <div class="source-timing"><label>Bắt đầu (giây)<input v-model.number="sourceDrafts[segment.id]!.start" :disabled="busy" type="number" min="0" step="0.001" /></label><label>Kết thúc (giây)<input v-model.number="sourceDrafts[segment.id]!.end" :disabled="busy" type="number" min="0" step="0.001" /></label></div>
            <p v-if="sourceChanged(segment) && sourceEditBlock(sourceDrafts[segment.id]!, job.metadata?.duration)" class="micro warn-text">{{ sourceEditBlock(sourceDrafts[segment.id]!, job.metadata?.duration) }}</p>
            <button class="btn small" :disabled="busy || !sourceChanged(segment) || !!sourceEditBlock(sourceDrafts[segment.id]!, job.metadata?.duration)" @click="$emit('saveSource', segment)"><StudioIcon name="check" :size="15" /> Lưu lời gốc và thời gian</button>
          </div>
          <label>Bản dịch tiếng Việt
            <textarea v-model="drafts[segment.id]" :disabled="busy" maxlength="2000" rows="3" />
          </label>
        </div>
        <p v-if="segment.warning" class="notice warn"><StudioIcon name="alert" :size="17" /> {{ segment.warning }}</p>
        <p v-if="segment.translation_stale || segment.translation_needs_review" class="notice warn"><StudioIcon name="alert" :size="17" /> {{ segment.translation_stale ? 'Lời gốc đã thay đổi. Cập nhật bản dịch đoạn này trước khi tạo giọng.' : 'Lời gốc hoặc ngữ cảnh đã thay đổi. Rà soát bản dịch đoạn này.' }}</p>
        <div class="segment-actions">
          <button class="btn small" :disabled="busy || !hasDraftChange(drafts[segment.id], segment.translation) || !drafts[segment.id]?.trim()" @click="$emit('save', segment)">
            <StudioIcon name="check" :size="15" /> Lưu đoạn
          </button>
          <button class="btn small" :disabled="busy || !ready || sourceChanged(segment) || hasDraftChange(drafts[segment.id], segment.translation)" @click="$emit('translate', segment)">
            <StudioIcon name="refresh" :size="15" /> Dịch lại
          </button>
          <button class="btn small" :disabled="busy || !ready || sourceChanged(segment) || !drafts[segment.id]?.trim()" @click="$emit('regenerate', segment)">
            <StudioIcon name="wave" :size="16" /> {{ hasDraftChange(drafts[segment.id], segment.translation) ? 'Lưu và tạo giọng' : 'Tạo lại giọng' }}
          </button>
          <button v-if="segment.audio_path" class="btn small" :disabled="hasDraftChange(drafts[segment.id], segment.translation)" @click="$emit('preview', segment)">
            <StudioIcon name="volume" :size="16" /> Nghe đoạn
          </button>
          <button class="btn small" :disabled="busy || sourceChanged(segment) || hasDraftChange(drafts[segment.id], segment.translation) || !segment.translation?.trim() || job.legacy" @click="$emit('importAudio', segment)"><StudioIcon name="folder" :size="16" /> Nhập WAV cho đoạn</button>
        </div>
        <audio v-if="audio?.id === segment.id" :key="audio.url" :src="audio.url" controls autoplay
          :aria-label="'Âm thanh đoạn ' + (index + 1)" @error="$emit('audioError')" />
      </li>
    </ol>
    <nav v-if="pageCount > 1" class="transcript-pagination" aria-label="Trang tiếp theo của lời thoại">
      <span class="micro">Trang {{ page }} / {{ pageCount }}</span><div><button class="btn small" :disabled="page <= 1" @click="turnPage(-1)">Trang trước</button><button class="btn small" :disabled="page >= pageCount" @click="turnPage(1)">Trang sau</button></div>
    </nav>
  </template>
</template>
