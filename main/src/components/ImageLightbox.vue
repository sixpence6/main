<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  src: string
  alt: string
}>()
const emit = defineEmits<{ close: [] }>()

const layer = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
let inerted: Array<{ element: HTMLElement; hadInert: boolean }> = []

function requestClose(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    requestClose()
    return
  }
  if (event.key === 'Tab') {
    event.preventDefault()
    closeButton.value?.focus()
  }
}

function setBackgroundInert(): void {
  inerted = []
  for (const child of Array.from(document.body.children)) {
    if (!(child instanceof HTMLElement)) continue
    if (child === layer.value || child.tagName === 'SCRIPT' || child.tagName === 'STYLE') continue
    inerted.push({ element: child, hadInert: child.hasAttribute('inert') })
    child.setAttribute('inert', '')
  }
}

function restoreBackground(): void {
  for (const entry of inerted) {
    if (!entry.hadInert) entry.element.removeAttribute('inert')
  }
  inerted = []
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousFocus = document.activeElement as HTMLElement | null
      previousOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      setBackgroundInert()
      closeButton.value?.focus()
    } else {
      document.removeEventListener('keydown', onKeydown)
      document.body.style.overflow = previousOverflow
      restoreBackground()
      await nextTick()
      if (previousFocus?.isConnected) previousFocus.focus()
      previousFocus = null
    }
  },
  { flush: 'post' },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = previousOverflow
  restoreBackground()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="lightbox-fade">
      <div v-if="open" ref="layer" class="lightbox-layer" @click.self="requestClose">
        <div class="lightbox-scrim" aria-hidden="true" @click="requestClose" />
        <div class="lightbox-panel" role="dialog" aria-modal="true" aria-label="图片大图">
          <img class="lightbox-image" :src="src" :alt="alt">
          <button
            ref="closeButton"
            type="button"
            class="lightbox-close"
            aria-label="关闭大图"
            title="关闭大图"
            @click="requestClose"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.lightbox-layer {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 16px;
  transition: opacity var(--dur-base) var(--ease-out);
}
.lightbox-scrim {
  position: absolute;
  inset: 0;
  background: var(--scrim);
}
.lightbox-panel {
  position: relative;
  max-width: calc(100vw - 32px);
  padding: 12px;
  border-radius: var(--radius-lg);
  background: var(--surface-panel);
  box-shadow: var(--shadow-overlay);
}
.lightbox-image {
  display: block;
  width: auto;
  height: auto;
  max-width: min(92vw, 1120px);
  max-height: 86vh;
  object-fit: contain;
  border-radius: var(--radius-sm);
}
.lightbox-close {
  position: absolute;
  top: 8px;
  right: 8px;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  color: var(--text-primary);
  cursor: pointer;
}
.lightbox-close:hover { border-color: var(--line-hover); }
.lightbox-close:focus-visible {
  outline: 2px solid var(--focus-ring-light);
  outline-offset: 2px;
}
.lightbox-close svg { width: 20px; height: 20px; }
.lightbox-fade-enter-active,
.lightbox-fade-leave-active { transition: opacity var(--dur-base) var(--ease-out); }
.lightbox-fade-enter-from,
.lightbox-fade-leave-to { opacity: 0; }
@media (max-width: 767px) {
  .lightbox-layer { padding: 16px; }
  .lightbox-panel { padding: 8px; }
}
@media (prefers-reduced-motion: reduce) {
  .lightbox-layer,
  .lightbox-fade-enter-active,
  .lightbox-fade-leave-active { transition: none; }
}
</style>
