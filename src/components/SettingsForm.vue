<script setup lang="ts">
import StudioIcon from './StudioIcon.vue'
import type { Settings } from '../workflow'

const settings = defineModel<Settings>({ required: true })
defineProps<{ busy: boolean; dirty: boolean }>()
defineEmits<{ save: []; chooseDirectory: [target: 'projects_dir'] }>()
const devices = [{ key: 'asr_device', label: 'Nhận dạng (Whisper)' }, { key: 'translation_device', label: 'Dịch thoại (Ollama)' }, { key: 'tts_device', label: 'Tạo giọng (TTS)' }] as const
</script>

<template>
  <form class="settings-form" @submit.prevent="$emit('save')">
    <fieldset :disabled="busy" class="split">
      <section class="panel form-grid">
        <div class="panel-title"><h2><StudioIcon name="folder" /> Đường dẫn công cụ</h2></div>
        <div>
          <label for="projects-directory">Thư mục gốc chứa dự án video</label>
          <div class="input-row"><input id="projects-directory" v-model.trim="settings.projects_dir" placeholder="Videos / AI Video Dubbing" autocomplete="off" spellcheck="false" /><button class="btn" type="button" @click="$emit('chooseDirectory', 'projects_dir')"><StudioIcon name="folder" :size="17" /> Duyệt</button></div>
          <p class="micro">Áp dụng cho dự án mới. Các dự án đã lưu tiếp tục dùng thư mục của chúng.</p>
          <p class="micro">Video xuất được lưu theo phiên bản trong thư mục <code>07-output</code> của từng dự án.</p>
        </div>
        <label>Python 3.11 trong môi trường ảo
          <input v-model.trim="settings.python_path" placeholder="Tự tìm môi trường ảo của ứng dụng" autocomplete="off" spellcheck="false" />
        </label>
        <label>FFmpeg
          <input v-model.trim="settings.ffmpeg_path" placeholder="ffmpeg.exe trong PATH" autocomplete="off" spellcheck="false" />
        </label>
        <label>FFprobe
          <input v-model.trim="settings.ffprobe_path" placeholder="ffprobe.exe trong PATH" autocomplete="off" spellcheck="false" />
        </label>
      </section>
      <section class="panel form-grid">
        <div class="panel-title"><h2><StudioIcon name="cpu" /> Mô hình và thiết bị</h2></div>
        <label>Mô hình Ollama / Qwen
          <select v-model="settings.ollama_model"><option value="qwen2.5:3b">Qwen 2.5 · 3B (mặc định)</option><option value="qwen2.5:7b">Qwen 2.5 · 7B</option><option v-if="!['qwen2.5:3b', 'qwen2.5:7b'].includes(settings.ollama_model)" :value="settings.ollama_model">{{ settings.ollama_model }}</option></select>
          <span class="micro">Thiết lập tự động tải mô hình đang chọn. So sánh nội dung và tốc độ trên video của bạn.</span>
        </label>
        <label>Mô hình Whisper
          <input v-model.trim="settings.whisper_model" required placeholder="small" autocomplete="off" spellcheck="false" />
          <span class="micro">Tên mô hình dùng cho Thiết lập tự động, ví dụ small hoặc base.</span>
        </label>
        <label>Đường dẫn mô hình VieNeu-TTS
          <input v-model.trim="settings.tts_model_path" placeholder="Dùng mô hình từ Thiết lập tự động" autocomplete="off" spellcheck="false" />
        </label>
        <label v-for="device in devices" :key="device.key">{{ device.label }}
          <select v-model="settings[device.key]"><option value="auto">Tự động</option><option value="cpu">Chỉ CPU</option><option value="gpu">Yêu cầu GPU tương thích</option></select>
        </label>
        <label class="checkbox-field">
          <input v-model="settings.verify_packets" type="checkbox" />
          <span>Đối chiếu mã băm gói hình khi xuất
            <span class="micro">Đọc thêm luồng hình để xác minh các gói video được giữ nguyên.</span>
          </span>
        </label>
      </section>
    </fieldset>
    <div class="panel-actions settings-actions">
      <span class="micro" aria-live="polite">{{ dirty ? 'Có thay đổi chưa lưu.' : 'Thiết lập được lưu trên máy này.' }}</span>
      <button class="btn primary" type="submit" :disabled="busy || !dirty">
        <StudioIcon name="check" :size="17" /> Lưu và kiểm tra lại
      </button>
    </div>
  </form>
</template>
