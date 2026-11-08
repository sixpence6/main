<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    closeLabel: string
    width?: string
    description?: string
    panelId?: string
  }>(),
  { width: '400px', description: '' },
)
const emit = defineEmits<{ close: [] }>()

const titleId = `drawer-title-${useId()}`
const descriptionId = `drawer-description-${useId()}`
const layer = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
let inerted: Array<{ element: HTMLElement; hadInert: boolean }> = []

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

function focusableElements(): HTMLElement[] {
  if (!panel.value) return []
  return Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.getAttribute('aria-hidden') !== 'true')
}

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
  if (event.key !== 'Tab') return
  const items = focusableElements()
  if (!items.length) {
    event.preventDefault()
    panel.value?.focus()
    return
  }
  const first = items[0]!
  const last = items[items.length - 1]!
  const active = document.activeElement
  if (event.shiftKey && (active === first || !panel.value?.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !panel.value?.contains(active))) {
    event.preventDefault()
    first.focus()
  }
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
      const items = focusableElements()
      ;(items[0] ?? panel.value)?.focus()
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
    <Transition name="drawer-slide">
      <div v-if="open" ref="layer" class="drawer-layer">
        <div class="drawer-backdrop" @click.self="requestClose" />
        <aside
          ref="panel"
          :id="panelId"
          class="drawer-panel"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="description ? descriptionId : undefined"
          :style="{ width }"
          tabindex="-1"
        >
          <header class="drawer-head">
            <div>
              <h2 :id="titleId">{{ title }}</h2>
              <p v-if="description" :id="descriptionId" class="drawer-description">
                {{ description }}
              </p>
            </div>
            <button type="button" class="icon-button drawer-close" :aria-label="closeLabel" :title="closeLabel" @click="requestClose">
              <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>
          <div class="drawer-content">
            <slot />
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>
