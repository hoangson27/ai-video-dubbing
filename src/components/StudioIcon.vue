<script setup lang="ts">
const paths = {
  wave: 'M2 12h3l2-5 3 10 3-14 3 17 2-8h4',
  film: 'M4 3h16v18H4zM4 8h4m8 0h4M4 16h4m8 0h4M8 3v18m8-18v18',
  lines: 'M4 6h16M4 10h12M4 14h16M4 18h9',
  pulse: 'M3 12h4l3-7 4 14 3-7h4',
  gear: 'M12 3l1.4 2.4 2.8.4 2 2-.4 2.8L20 12l-2.2 1.4.4 2.8-2 2-2.8-.4L12 20l-1.4-2.2-2.8.4-2-2 .4-2.8L4 12l2.2-1.4-.4-2.8 2-2 2.8-.4zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  folder: 'M3 7V5h7l2 2h9v12H3zM3 10h18',
  play: 'm9 5 11 7-11 7z',
  stop: 'M6 6h12v12H6z',
  check: 'm5 12 5 5L20 7',
  alert: 'M12 8v5m0 3v.1M12 3 2 21h20z',
  refresh: 'M20 11a8 8 0 1 0-2 6M20 5v6h-6',
  file: 'M6 3h8l4 4v14H6zM14 3v5h4m-8 5h4m-4 4h4',
  clock: 'M12 8v4l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z',
  download: 'M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4',
  shield: 'M12 3l8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM9 12l2 2 4-4',
  volume: 'M3 9h4l5-4v14l-5-4H3zM16 9a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  close: 'm6 6 12 12M18 6 6 18',
  activity: 'M4 4h16v16H4zM8 15l3-4 3 2 3-5',
  cpu: 'M7 7h10v10H7zM10 10h4v4h-4zM9 3v4m6-4v4M9 17v4m6-4v4M3 9h4m-4 6h4m10-6h4m-4 6h4',
}

defineProps<{ name: keyof typeof paths; size?: number }>()
</script>

<template>
  <svg aria-hidden="true" :width="size ?? 20" :height="size ?? 20" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">
    <path :d="paths[name]" />
  </svg>
</template>
