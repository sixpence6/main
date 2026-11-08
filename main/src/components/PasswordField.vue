<script setup lang="ts">
import { computed, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    modelValue: string
    name: string
    autocomplete: string
    hint?: string
    error?: string | null
    disabled?: boolean
    required?: boolean
    minlength?: number
    maxlength?: number
    inputmode?: 'text' | 'email' | 'numeric'
    describedBy?: string
  }>(),
  {
    hint: '',
    error: null,
    disabled: false,
    required: false,
    minlength: undefined,
    maxlength: undefined,
    inputmode: 'text',
    describedBy: '',
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const inputId = useId()
const hintId = inputId + '-hint'
const errorId = inputId + '-error'
const visible = ref(false)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(hintId)
  if (props.error) ids.push(errorId)
  if (props.describedBy) ids.push(props.describedBy)
  return ids.length > 0 ? ids.join(' ') : undefined
})

function update(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="password-field">
    <label :for="inputId" class="password-label">
      {{ label }}
    </label>
    <div class="password-control" :class="{ 'password-control-invalid': error }">
      <input
        :id="inputId"
        :value="modelValue"
        :type="visible ? 'text' : 'password'"
        :name="name"
        :autocomplete="autocomplete"
        :inputmode="inputmode"
        :minlength="minlength"
        :maxlength="maxlength"
        :required="required"
        :disabled="disabled"
        :aria-invalid="error ? 'true' : undefined"
        :aria-describedby="describedBy"
        autocapitalize="off"
        spellcheck="false"
        @input="update"
      />
      <button
        class="password-toggle"
        type="button"
        :aria-label="(visible ? '隐藏' : '显示') + label"
        :aria-pressed="visible"
        :aria-controls="inputId"
        :disabled="disabled"
        @click="visible = !visible"
      >
        {{ visible ? '隐藏' : '显示' }}
      </button>
    </div>
    <p v-if="hint" :id="hintId" class="password-hint">{{ hint }}</p>
    <p v-if="error" :id="errorId" class="password-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.password-field {
  display: grid;
  gap: 8px;
}
.password-label {
  color: var(--text-muted);
  font-size: var(--text-body);
}
.password-control {
  display: flex;
  align-items: stretch;
  overflow: hidden;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  transition: border-color var(--dur-base) var(--ease-out);
}
.password-control:focus-within {
  border-color: var(--action-primary);
  outline: 2px solid var(--focus-ring-light);
  outline-offset: 2px;
}
.password-control-invalid {
  border-color: var(--state-danger);
}
.password-control-invalid:focus-within {
  border-color: var(--state-danger);
  outline: 2px solid var(--focus-ring-light);
  outline-offset: 2px;
}
.password-control input {
  width: 100%;
  min-width: 0;
  min-height: 46px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--text-reading);
}
.password-toggle {
  flex: none;
  min-width: 58px;
  padding: 0 12px;
  border: 0;
  border-left: 1px solid var(--line-hairline);
  background: transparent;
  color: var(--action-primary);
  font-size: 12px;
}
.password-toggle:hover:not(:disabled) {
  background: var(--action-soft);
}
.password-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.password-hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.65;
}
.password-error {
  margin: 0;
  color: var(--state-danger);
  font-size: 12px;
  line-height: 1.6;
}
</style>
