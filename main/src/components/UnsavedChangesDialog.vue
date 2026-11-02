<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  destination: string
  draftDirty: boolean
  attachmentPending: boolean
}>()

const emit = defineEmits<{ cancel: []; confirm: [] }>()

const dialogEl = ref<HTMLElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''

function focusTargets(): HTMLElement[] {
  const root = dialogEl.value
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, textarea, select'))
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
    return
  }
  if (event.key !== 'Tab') return
  const targets = focusTargets()
  if (!targets.length) {
    event.preventDefault()
    return
  }
  const first = targets[0]
  const last = targets[targets.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (event.shiftKey && (active === first || !rootContains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !rootContains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function rootContains(element: HTMLElement | null): boolean {
  return Boolean(element && dialogEl.value?.contains(element))
}

function lockPage(): void {
  if (typeof document === 'undefined') return
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', onKeydown)
}

function unlockPage(): void {
  if (typeof document === 'undefined') return
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', onKeydown)
  const target = previousFocus
  previousFocus = null
  if (target?.isConnected) void nextTick(() => target.focus())
}

watch(
  () => props.open,
  async (open, wasOpen) => {
    if (open) {
      lockPage()
      await nextTick()
      cancelButton.value?.focus()
    } else if (wasOpen) {
      unlockPage()
    }
  },
)

onBeforeUnmount(() => {
  if (props.open) unlockPage()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="unsaved-layer" @click.self="emit('cancel')">
      <div class="unsaved-backdrop" aria-hidden="true" @click="emit('cancel')"></div>
      <div
        ref="dialogEl"
        class="unsaved-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="unsaved-title"
        aria-describedby="unsaved-description"
      >
        <h2 id="unsaved-title">放弃未保存的修改？</h2>
        <p id="unsaved-description">
          将前往「{{ destination }}」，当前页面中未保存的内容会被放弃。
        </p>
        <ul v-if="draftDirty || attachmentPending" class="unsaved-list">
          <li v-if="draftDirty">输入框中尚未发送的文字</li>
          <li v-if="attachmentPending">尚未上传完成的参考图</li>
        </ul>
        <p class="unsaved-hint">这些内容只保留在当前页面内存中，离开后无法恢复。</p>
        <div class="unsaved-actions">
          <button ref="cancelButton" type="button" class="secondary-button" @click="emit('cancel')">
            继续编辑
          </button>
          <button type="button" class="button-danger" @click="emit('confirm')">放弃修改</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
