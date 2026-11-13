<script setup lang="ts">
import StudioIcon from './StudioIcon.vue'
import { timestamp } from '../workflow'
import type { Metadata } from '../workflow'

defineProps<{
  filename: string
  metadata: Metadata | null
  videoUrl: string
  videoError: boolean
  usingProxy: boolean
  busy: boolean
  probing: boolean
}>()
defineEmits<{ pick: []; failed: []; proxy: [] }>()

function size(bytes: number) {
  return new Intl.NumberFormat('vi', { maximumFractionDigits: 1 }).format(bytes / 1024 ** 2) + ' MB'
}
</script>

<template>
  <section class="panel source-panel" aria-labelledby="source-heading">
    <div class="panel-title flex items-center justify-between gap-3">
      <h2 id="source-heading"><StudioIcon name="film" /> Video nguồn</h2>
      <button v-if="filename" type="button" class="btn small" :disabled="busy" @click="$emit('pick')">
        <StudioIcon name="folder" :size="16" /> Đổi video
      </button>
    </div>
    <div v-if="filename" class="source-file">
      <StudioIcon name="file" :size="17" /> <strong :title="filename">{{ filename }}</strong>
      <span v-if="metadata">{{ size(metadata.size) }}</span>
    </div>
    <div class="viewport">
      <video v-if="videoUrl && !videoError" :key="videoUrl" :src="videoUrl" controls preload="metadata"
        aria-label="Xem trước video nguồn" @error="$emit('failed')" />
      <div v-else class="viewport-empty">
        <div class="film-mark"><StudioIcon :name="probing ? 'clock' : 'film'" :size="32" /></div>
        <template v-if="probing">
          <h3>Đang đọc thông tin video</h3>
        </template>
        <template v-else-if="filename && videoError">
          <h3>Không phát được video trực tiếp</h3>
          <p v-if="usingProxy">Windows không phát được bản xem trước của tệp này.</p>
          <p v-else>Codec hoặc định dạng này cần bản xem trước tương thích.</p>
          <button v-if="!usingProxy" type="button" class="btn" :disabled="busy" @click="$emit('proxy')">
            <StudioIcon name="play" :size="16" /> Tạo bản xem trước 90 giây đầu
          </button>
        </template>
        <template v-else-if="!filename">
          <h3>Kéo video vào cửa sổ này</h3>
          <p>MP4, MKV hoặc MOV</p>
          <button type="button" class="btn" :disabled="busy" @click="$emit('pick')">
            <StudioIcon name="folder" :size="17" /> Chọn video
          </button>
        </template>
      </div>
    </div>
    <p v-if="usingProxy && !videoError" class="micro mt-2">Bản xem trước tương thích hiển thị tối đa 90 giây đầu.</p>
    <dl v-if="metadata" class="metadata">
      <div><dt>Độ phân giải</dt><dd>{{ metadata.width }} × {{ metadata.height }}</dd></div>
      <div><dt>Tốc độ khung hình</dt><dd>{{ metadata.fps ? metadata.fps.toFixed(2) + ' fps' : 'Chưa rõ' }}</dd></div>
      <div><dt>Thời lượng</dt><dd>{{ timestamp(metadata.duration, false) }}</dd></div>
      <div><dt>Codec video</dt><dd>{{ metadata.codec }}</dd></div>
    </dl>
    <div v-if="metadata?.audio_tracks.length" class="tracks">
      <span class="micro">Track âm thanh</span>
      <span v-for="(track, i) in metadata.audio_tracks" :key="track.index" class="track-tag">
        {{ i + 1 }}. {{ track.codec || 'Chưa rõ codec' }}<template v-if="track.channels"> · {{ track.channels }} kênh</template>
        <template v-if="track.language && track.language !== 'und'"> · {{ track.language }}</template>
      </span>
    </div>
    <p v-else-if="metadata" class="notice warn mt-3"><StudioIcon name="alert" :size="18" /> Video này không có track âm thanh.</p>
  </section>
</template>
