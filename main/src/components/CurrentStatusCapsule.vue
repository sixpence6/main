<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useLiveStatus } from '@/composables/useLiveStatus'

const { status } = useLiveStatus()
const hovered = ref(false)
const pinned = ref(false)
const elapsed = ref('')
let elapsedTimer: ReturnType<typeof setInterval> | null = null

const expanded = computed(() => hovered.value || pinned.value)
const tone = computed(() => status.value?.tone ?? 'idle')
const label = computed(() => status.value?.label ?? '准备开始')
const detail = computed(() => status.value?.detail ?? '描述商品和画面，我会在这里同步进度')
const title = computed(() => status.value?.projectTitle ?? '新对话')
const busy = computed(() => Boolean(status.value?.busy))

function formatElapsed(from: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - from) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h`
}

function stopElapsed(): void {
  if (elapsedTimer !== null) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function syncElapsed(): void {
  stopElapsed()
  if (!status.value?.busy) {
    elapsed.value = ''
    return
  }
  const startedAt = status.value.updatedAt
  elapsed.value = formatElapsed(startedAt)
  elapsedTimer = setInterval(() => {
    if (!status.value?.busy) {
      stopElapsed()
      elapsed.value = ''
      return
    }
    elapsed.value = formatElapsed(startedAt)
  }, 1000)
}

watch(
  () => [status.value?.busy, status.value?.updatedAt] as const,
  syncElapsed,
  { immediate: true },
)

onBeforeUnmount(stopElapsed)

function togglePinned(): void {
  pinned.value = !pinned.value
}
</script>

<template>
  <div class="status-capsule-anchor">
    <button
      type="button"
      class="status-capsule"
      :class="[`is-${tone}`, { 'is-expanded': expanded, 'is-busy': busy }]"
      :aria-expanded="expanded"
      :aria-label="`当前状态：${label}。${detail}`"
      @pointerenter="hovered = true"
      @pointerleave="hovered = false"
      @focus="hovered = true"
      @blur="hovered = false"
      @click="togglePinned"
    >
      <span class="status-orb" aria-hidden="true"><i /></span>
      <span class="status-copy">
        <span class="status-kicker">
          当前状态
          <span v-if="busy && elapsed" class="status-elapsed">{{ elapsed }}</span>
        </span>
        <strong :key="label" class="status-label">{{ label }}</strong>
        <span class="status-detail-wrap">
          <span class="status-detail">{{ detail }}</span>
        </span>
      </span>
      <span class="status-context" aria-hidden="true">{{ title }}</span>
      <svg class="status-chevron" aria-hidden="true" viewBox="0 0 16 16" width="14" height="14">
        <path d="M4 6.5 8 10l4-3.5" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.7" />
      </svg>
    </button>
    <span class="sr-only" aria-live="polite">{{ label }}。{{ detail }}</span>
  </div>
</template>

<style scoped>
.status-capsule-anchor {
  position: absolute;
  z-index: 24;
  top: 13px;
  left: 50%;
  width: min(336px, calc(100% - 48px));
  transform: translateX(-50%);
  pointer-events: none;
}
.status-capsule {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 46px;
  padding: 7px 13px 7px 11px;
  border: 1px solid rgba(255, 255, 255, .9);
  border-radius: 17px;
  background:
    linear-gradient(145deg, rgba(255, 255, 255, .97), rgba(248, 248, 253, .91));
  box-shadow:
    0 12px 32px rgba(37, 39, 72, .13),
    0 2px 7px rgba(37, 39, 72, .08),
    inset 0 1px 0 rgba(255, 255, 255, 1);
  color: var(--color-text);
  text-align: left;
  pointer-events: auto;
  transform: translateZ(0);
  transition:
    border-radius .38s var(--ease-spring),
    box-shadow .28s ease,
    background-color .28s ease,
    transform .28s var(--ease-spring);
  -webkit-backdrop-filter: blur(20px) saturate(1.35);
  backdrop-filter: blur(20px) saturate(1.35);
}
.status-capsule:hover,
.status-capsule.is-expanded {
  border-radius: 21px;
  box-shadow:
    0 18px 42px rgba(37, 39, 72, .17),
    0 3px 9px rgba(37, 39, 72, .09),
    inset 0 1px 0 rgba(255, 255, 255, 1);
}
.status-capsule:active { transform: scale(.985); }
.status-orb {
  position: relative;
  display: grid;
  place-items: center;
  width: 27px;
  height: 27px;
  flex: none;
  border-radius: 10px;
  background: #f0f1f7;
  color: var(--color-text-muted);
  transition: background-color .3s ease, color .3s ease;
}
.status-orb::before {
  content: "";
  position: absolute;
  inset: 2px;
  border-radius: 8px;
  border: 1.5px solid currentColor;
  opacity: .18;
}
.status-orb i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 14%, transparent);
}
.is-working .status-orb { background: var(--color-primary-soft); color: var(--color-primary); }
.is-waiting .status-orb { background: #fff5df; color: var(--color-warning); }
.is-success .status-orb { background: #eaf7f0; color: var(--color-success); }
.is-error .status-orb { background: #fff0f2; color: var(--color-danger); }
.is-busy .status-orb::before {
  border-color: transparent;
  border-top-color: currentColor;
  border-right-color: currentColor;
  opacity: .9;
  animation: status-spin .85s linear infinite;
}
.is-busy .status-orb i { animation: status-breathe 1.4s ease-in-out infinite; }
.status-copy { display: grid; min-width: 0; gap: 1px; }
.status-kicker {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-muted);
  font-size: 9px;
  line-height: 1.25;
  letter-spacing: .11em;
  text-transform: uppercase;
}
.status-elapsed {
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--color-panel-soft);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  letter-spacing: .02em;
}
.status-label {
  display: block;
  overflow: hidden;
  color: var(--color-text);
  font-size: 12.5px;
  font-weight: 650;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
  animation: status-label-in .28s var(--ease-spring) both;
}
.status-detail-wrap {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  transform: translateY(-2px);
  transition:
    grid-template-rows .34s var(--ease-spring),
    opacity .2s ease,
    transform .34s var(--ease-spring);
}
.status-capsule:hover .status-detail-wrap,
.status-capsule:focus-visible .status-detail-wrap,
.status-capsule.is-expanded .status-detail-wrap {
  grid-template-rows: 1fr;
  opacity: 1;
  transform: translateY(0);
}
.status-detail {
  display: block;
  max-height: 34px;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 10.5px;
  line-height: 1.45;
}
.status-context {
  max-width: 82px;
  overflow: hidden;
  padding: 3px 7px;
  border: 1px solid var(--color-border-soft);
  border-radius: 999px;
  background: rgba(247, 247, 252, .78);
  color: var(--color-text-muted);
  font-size: 9.5px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-chevron {
  flex: none;
  color: var(--color-text-muted);
  opacity: .65;
  transition: transform .32s var(--ease-spring), opacity .2s ease;
}
.status-capsule.is-expanded .status-chevron { transform: rotate(180deg); opacity: 1; }
@keyframes status-label-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes status-spin { to { transform: rotate(360deg); } }
@keyframes status-breathe { 0%, 100% { transform: scale(.82); opacity: .65; } 50% { transform: scale(1.08); opacity: 1; } }
@media (max-width: 900px) {
  .status-capsule-anchor { width: min(280px, calc(100% - 32px)); }
  .status-context { display: none; }
}
@media (max-width: 767px) {
  .status-capsule-anchor {
    top: 7px;
    right: 8px;
    left: auto;
    width: min(210px, calc(100% - 108px));
    transform: none;
  }
  .status-capsule { min-height: 40px; padding: 5px 10px 5px 8px; }
  .status-kicker { font-size: 8px; }
  .status-label { font-size: 11.5px; }
}
@media (prefers-reduced-motion: reduce) {
  .status-capsule,
  .status-orb,
  .status-detail-wrap,
  .status-chevron { transition: none; }
  .is-busy .status-orb::before,
  .is-busy .status-orb i { animation: none; }
}
</style>
