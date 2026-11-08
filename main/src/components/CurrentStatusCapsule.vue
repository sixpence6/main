<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLiveStatus, type LiveStatusTone } from '@/composables/useLiveStatus'

const props = withDefaults(defineProps<{ variant?: 'topbar' | 'inline' }>(), {
  variant: 'topbar',
})

const { status } = useLiveStatus()
const tone = computed<LiveStatusTone>(() => status.value?.tone ?? 'idle')
const label = computed(() => status.value?.label ?? '空闲')
const detail = computed(() => status.value?.detail ?? '')

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

function formatElapsed(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

const elapsed = computed(() => {
  if (!status.value) return ''
  const ms = now.value - status.value.updatedAt
  return ms >= 1000 ? formatElapsed(ms) : ''
})

const title = computed(() => {
  const base = detail.value ? `${label.value}：${detail.value}` : label.value
  return elapsed.value ? `${base}（已持续 ${elapsed.value}）` : base
})

watch(status, () => {
  now.value = Date.now()
})

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="status-capsule-anchor" :class="`is-${props.variant}`">
    <div
      class="status-capsule"
      :class="`is-${tone}`"
      role="status"
      aria-live="polite"
      :title="title"
    >
      <svg
        v-if="tone === 'working'"
        class="status-icon"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        aria-hidden="true"
      >
        <path d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5" />
        <path d="M8 13.5A5.5 5.5 0 0 1 2.5 8" opacity="0.45" />
      </svg>
      <svg
        v-else-if="tone === 'waiting'"
        class="status-icon"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="5.5" />
        <path d="M8 4.8v3.4l2.2 1.3" />
      </svg>
      <svg
        v-else-if="tone === 'success'"
        class="status-icon"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M3 8.4l3.1 3.1L13 4.8" />
      </svg>
      <svg
        v-else-if="tone === 'error'"
        class="status-icon"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        aria-hidden="true"
      >
        <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
      </svg>
      <svg
        v-else
        class="status-icon"
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="5" />
      </svg>
      <span class="status-label">{{ label }}</span>
      <time v-if="elapsed" class="status-time" aria-hidden="true">{{ elapsed }}</time>
    </div>
  </div>
</template>

<style scoped>
.status-capsule-anchor { display: flex; align-items: center; min-width: 0; }
.status-capsule-anchor.is-inline { justify-content: flex-start; }
.status-capsule {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
  max-width: 100%;
  padding: 0 var(--space-2);
  border: 1px solid var(--line-inverse);
  border-radius: var(--radius-pill);
  background: var(--surface-shell-active);
  color: var(--text-inverse-secondary);
  transition: color var(--dur-fast) var(--ease-out), opacity var(--dur-fast) var(--ease-out);
}
.status-icon { flex: none; width: 16px; height: 16px; color: var(--text-inverse-secondary); }
.status-label {
  overflow: hidden;
  font-size: var(--text-caption);
  font-weight: 500;
  line-height: var(--text-caption-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-time {
  flex: none;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  font-variant-numeric: tabular-nums;
  line-height: var(--text-caption-line);
}
.status-capsule-anchor.is-inline .status-capsule {
  border-color: var(--line-hairline);
  background: var(--surface-panel);
  color: var(--text-secondary);
}
.status-capsule-anchor.is-inline .status-icon { color: var(--text-secondary); }
.status-capsule-anchor.is-inline .is-working .status-icon { color: var(--action-link); }
.status-capsule-anchor.is-inline .is-waiting .status-icon { color: var(--state-warning); }
.status-capsule-anchor.is-inline .is-success .status-icon { color: var(--state-success); }
.status-capsule-anchor.is-inline .is-error .status-icon { color: var(--state-danger); }
.status-capsule-anchor.is-inline .status-time { color: var(--text-muted); }

@media (prefers-reduced-motion: reduce) {
  .status-capsule { transition: none; }
}
</style>
