import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./main/src', import.meta.url)),
    },
    // main/ has its own node_modules; without dedupe, App and ChatPage can
    // resolve different vue-router copies and lose the router injection.
    dedupe: ['vue', 'vue-router'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.spec.ts'],
    css: false,
    restoreMocks: true,
    clearMocks: true,
  },
})
