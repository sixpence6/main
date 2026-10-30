<script setup lang="ts">
defineProps<{
  eyebrow?: string
  title: string
  titleId: string
  intro?: string
}>()
</script>

<template>
  <section class="app-page" :aria-labelledby="titleId">
    <div class="page-heading">
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h2 :id="titleId">{{ title }}</h2>
      <p v-if="intro" class="page-intro">{{ intro }}</p>
      <slot name="meta" />
    </div>

    <div class="page-body">
      <slot />
    </div>

    <footer v-if="$slots.actions" class="page-actions">
      <slot name="actions" />
    </footer>
  </section>
</template>
