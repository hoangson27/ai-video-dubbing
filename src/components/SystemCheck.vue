<script setup lang="ts">
import { computed, ref } from 'vue'
import StudioIcon from './StudioIcon.vue'
import { dependencySummary } from '../workflow'
import type { CheckReport, DependencyInstallation } from '../workflow'

const props = defineProps<{
  report: CheckReport | null
  busy: boolean
  checking: boolean
  installation: DependencyInstallation
  whisperModel: string
  ollamaModel: string
}>()
defineEmits<{ check: []; install: []; cancel: []; settings: []; home: [] }>()

const summary = computed(() => dependencySummary(props.report?.checks || []))
const installing = computed(() => props.installation.status === 'running')
const logElement = ref<HTMLElement | null>(null)
const copyStatus = ref('')

async function copySetupLog() {
  const contents = [props.installation.logPath, ...props.installation.logs].filter(Boolean).join('\n')
  if (!contents) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
    await navigator.clipboard.writeText(contents)
    copyStatus.value = 'Đã sao chép nhật ký.'
  } catch {
    const selection = window.getSelection()
    if (selection && logElement.value) {
      const range = document.createRange()
      range.selectNodeContents(logElement.value)
      selection.removeAllRanges()
      selection.addRange(range)
      try {
        if (document.execCommand('copy')) {
          copyStatus.value = 'Đã sao chép nhật ký.'
          return
        }
      } catch { /* Keep the selected log visible for manual copying. */ }
    }
    copyStatus.value = 'Hãy chọn nội dung nhật ký bên dưới và nhấn Ctrl+C để sao chép.'
  }
}

const statuses = {
  installed: 'Đã cài',
  missing: 'Còn thiếu',
  incompatible: 'Không tương thích',
  unchecked: 'Chưa kiểm tra',
}
</script>

<template>
  <div class="split system-split">
    <div class="grid content-start gap-4">
      <section class="panel dependency-setup" aria-labelledby="setup-heading">
        <div class="panel-title"><h2 id="setup-heading"><StudioIcon name="folder" /> Chuẩn bị môi trường</h2></div>
        <template v-if="report?.ready && !installing">
          <p class="setup-ready"><StudioIcon name="check" :size="19" /> Các thành phần bắt buộc đã sẵn sàng.</p>
          <div class="panel-actions">
            <button class="btn primary" :disabled="busy" @click="$emit('home')">Về bàn dựng <StudioIcon name="arrow" :size="17" /></button>
          </div>
        </template>
        <template v-else>
          <p>Chỉ cần bấm Thiết lập tự động. Ứng dụng sẽ chuẩn bị các phần mềm và mô hình cần thiết để lồng tiếng trên máy này.</p>
          <p class="setup-models">Nhận dạng: <strong>{{ whisperModel }}</strong> · Dịch: <strong>{{ ollamaModel }}</strong> · Giọng đọc: <strong>VieNeu-TTS</strong></p>
          <p id="install-disclosure" class="micro">Cài cho tài khoản hiện tại, không cần quyền quản trị. Cần Internet và vài GB dung lượng trống. Bấm nút bên dưới để tải và cài đặt theo cấu hình hiện tại.</p>
          <div class="panel-actions">
            <button v-if="!installing" class="btn primary install-button" :disabled="busy || !report" aria-describedby="install-disclosure" @click="$emit('install')">
              <StudioIcon name="play" :size="17" /> {{ installation.status === 'failed' || installation.status === 'cancelled' ? 'Tiếp tục thiết lập' : 'Thiết lập tự động' }}
            </button>
            <button v-else class="btn danger" :disabled="installation.cancelling || !installation.canCancel" @click="$emit('cancel')">
              <StudioIcon name="stop" :size="17" /> {{ installation.cancelling ? 'Đang dừng cài đặt' : 'Dừng cài đặt' }}
            </button>
          </div>
        </template>
        <div v-if="installation.status !== 'idle' || installation.logs.length" class="installation-progress">
          <div class="progress-label">
            <p role="status">{{ installation.message }}</p>
            <strong>{{ Math.round(installation.progress) }}%</strong>
          </div>
          <progress :value="installation.progress" max="100" aria-label="Tiến độ cài đặt môi trường" />
          <p v-if="installation.error" class="notice error setup-error">{{ installation.error }}</p>
          <p v-if="installation.logPath" class="micro setup-log-path">Nhật ký được lưu tại: <code>{{ installation.logPath }}</code></p>
          <details class="installation-logs" :open="installation.status === 'failed' || installation.status === 'cancelled'">
            <summary>Chi tiết thiết lập</summary>
            <pre ref="logElement" class="job-log" tabindex="0">{{ installation.logs.length ? installation.logs.join('\n') : 'Đang chờ thông tin từ trình cài đặt…' }}</pre>
            <button class="btn small" type="button" :disabled="!installation.logs.length" @click="copySetupLog">Sao chép nhật ký</button>
            <p v-if="copyStatus" class="micro" role="status">{{ copyStatus }}</p>
          </details>
        </div>
      </section>
      <section class="panel" aria-labelledby="dependency-heading">
        <div class="panel-title flex items-center justify-between gap-3">
          <h2 id="dependency-heading"><StudioIcon name="pulse" /> Phần mềm và mô hình</h2>
          <button class="btn small" :disabled="busy" @click="$emit('check')">
            <StudioIcon name="refresh" :size="16" /> {{ checking ? 'Đang kiểm tra' : 'Kiểm tra lại' }}
          </button>
        </div>
        <p v-if="report" class="dependency-summary" aria-live="polite">
          <span>{{ summary.installed }} đã cài</span>
          <span>{{ summary.missing }} còn thiếu</span>
          <span v-if="summary.incompatible">{{ summary.incompatible }} không tương thích</span>
          <span v-if="summary.unchecked">{{ summary.unchecked }} chưa kiểm tra</span>
        </p>
        <div v-if="report" class="check-rows" :aria-busy="checking">
          <div v-for="(item, index) in report.checks" :key="item.id || item.name + index" class="check-row">
            <div>
              <h3>{{ item.name }} <small v-if="item.required === false">Tùy chọn</small></h3>
              <p>{{ item.detail }}</p>
            </div>
            <span class="chip" :class="item.status === 'installed' ? 'good' : item.status === 'unchecked' ? 'neutral' : 'warn'">{{ statuses[item.status] }}</span>
          </div>
        </div>
        <p v-else class="empty-copy">{{ checking ? 'Đang kiểm tra các thành phần trên máy này…' : 'Chạy kiểm tra để xem máy đã có gì và còn thiếu gì.' }}</p>
        <div class="panel-actions">
          <button class="btn" :disabled="busy" @click="$emit('settings')"><StudioIcon name="gear" :size="17" /> Đường dẫn và mô hình</button>
        </div>
      </section>
    </div>
    <div class="grid content-start gap-4">
      <section class="panel" aria-labelledby="hardware-heading">
        <div class="panel-title"><h2 id="hardware-heading"><StudioIcon name="cpu" /> Phần cứng</h2></div>
        <dl class="hardware">
          <div><dt>CPU</dt><dd>{{ report?.hardware.cpu || 'Chưa xác định' }}</dd></div>
          <div><dt>RAM</dt><dd>{{ report?.hardware.ram_gb != null ? report.hardware.ram_gb + ' GB' : 'Chưa xác định' }}</dd></div>
          <div><dt>GPU</dt><dd>
            <template v-if="report?.hardware.gpus.length">
              <p v-for="(gpu, index) in report.hardware.gpus" :key="index">{{ gpu.name }}<span v-if="gpu.vram_mb"> ({{ gpu.vram_mb }} MB)</span></p>
            </template>
            <template v-else>Chưa có thông tin GPU</template>
          </dd></div>
          <div><dt>Thiết bị CUDA</dt><dd>{{ report?.hardware.cuda_devices ?? 'Chưa xác định' }}</dd></div>
        </dl>
      </section>
      <section class="panel setup-guide">
        <div class="panel-title"><h2><StudioIcon name="pulse" /> Đọc kết quả kiểm tra</h2></div>
        <p><strong>Đã cài:</strong> ứng dụng tìm thấy thành phần và kiểm tra được phiên bản hoặc tệp cần dùng.</p>
        <p><strong>Chưa kiểm tra:</strong> cần chuẩn bị thành phần phía trước, chẳng hạn Python hoặc môi trường riêng, để kiểm tra tiếp.</p>
        <p>Đã cài công cụ ở vị trí riêng? Chọn <strong>Đường dẫn và mô hình</strong> để cấu hình, rồi kiểm tra lại.</p>
      </section>
    </div>
  </div>
</template>
