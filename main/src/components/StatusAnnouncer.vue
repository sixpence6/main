<script setup lang="ts">
import { useWorkspace } from '@/composables/useWorkspace'

const { announcement, organizing } = useWorkspace()
</script>

<template>
  <div
    class="sr-only"
    :role="organizing ? undefined : 'status'"
    aria-live="polite"
    aria-atomic="true"
  >{{ announcement }}</div>
</template>
