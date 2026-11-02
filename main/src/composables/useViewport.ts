import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export type Breakpoint = 'narrow' | 'medium' | 'wide'

export function breakpointFor(width: number): Breakpoint {
  if (width < 768) return 'narrow'
  if (width < 1024) return 'medium'
  return 'wide'
}

/** 只负责视口宽度和断点，不承载业务状态。 */
export function useViewport() {
  const width = ref(typeof window === 'undefined' ? 1280 : window.innerWidth)
  const breakpoint = computed<Breakpoint>(() => breakpointFor(width.value))
  const isMobile = computed(() => breakpoint.value === 'narrow')
  const isCompact = computed(() => breakpoint.value !== 'wide')

  const onResize = (): void => {
    width.value = window.innerWidth
  }

  onMounted(() => window.addEventListener('resize', onResize, { passive: true }))
  onBeforeUnmount(() => window.removeEventListener('resize', onResize))

  return { width, breakpoint, isMobile, isCompact }
}
