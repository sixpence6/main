<script setup lang="ts">
import { useWorkspace } from '@/composables/useWorkspace'

const { announcement } = useWorkspace()
</script>

<template>
  <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ announcement }}</div>
</template>
