<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 32 })
</script>

<template>
  <span class="brand-mark" :style="{ width: size + 'px', height: size + 'px' }" aria-hidden="true">
    <svg viewBox="0 0 32 32" fill="none">
      <rect x="1.25" y="1.25" width="29.5" height="29.5" rx="9.25" stroke="currentColor" stroke-width="1.5" />
      <path d="M8 11.5V8h3.5M20.5 8H24v3.5M24 20.5V24h-3.5M11.5 24H8v-3.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
      <rect x="11" y="11" width="10" height="10" rx="2.4" stroke="currentColor" stroke-width="1.6" />
      <circle cx="14.2" cy="14.2" r="1.15" fill="currentColor" />
      <path d="M12 19.2l2.7-2.7 2.25 2.2 1.35-1.3 1.7 1.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M24.6 10.6l.55 1.55 1.55.55-1.55.55-.55 1.55-.55-1.55-1.55-.55 1.55-.55.55-1.55z" fill="currentColor" />
    </svg>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-grid;
  flex: none;
  place-items: center;
  color: var(--color-primary);
}
.brand-mark svg {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
