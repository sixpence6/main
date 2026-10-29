<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  versionNo: number | null
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

function rootContains(element: HTMLElement | null): boolean {
  return Boolean(element && dialogEl.value?.contains(element))
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
    <div v-if="open" class="risk-layer" @click.self="emit('cancel')">
      <div class="risk-backdrop" aria-hidden="true" @click="emit('cancel')"></div>
      <div
        ref="dialogEl"
        class="risk-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="generation-risk-title"
        aria-describedby="generation-risk-description generation-risk-accounting"
      >
        <h2 id="generation-risk-title">再次生成可能重复计费</h2>
        <p id="generation-risk-description">
          <template v-if="versionNo !== null">第 {{ versionNo }} 版</template>
          <template v-else>上一次</template>
          生成结果未确认，原请求可能已提交并已由模型服务计费。
        </p>
        <p id="generation-risk-accounting" class="risk-hint">
          再次生成会创建一个新的生成请求，不会重试或恢复上一次请求。请确认你已了解可能重复计费的风险。
        </p>
        <div class="risk-actions">
          <button ref="cancelButton" type="button" class="secondary-button" @click="emit('cancel')">
            返回查看
          </button>
          <button type="button" class="primary-button" @click="emit('confirm')">
            我已了解并再次生成
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
