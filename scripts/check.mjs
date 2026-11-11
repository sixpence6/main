import { existsSync, realpathSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'

const root = path.resolve(import.meta.dirname, '..')
const backend = path.join(root, 'backend')
const dryRun = process.argv.includes('--dry-run')
const full = process.argv.includes('--full')

const npm =
  process.platform === 'win32' ? (process.env.ComSpec ?? 'cmd.exe') : 'npm'
const npmArgs = (args) =>
  process.platform === 'win32'
    ? ['/d', '/s', '/c', `npm ${args.join(' ')}`]
    : args
const python =
  process.platform === 'win32'
    ? path.join(backend, '.venv', 'Scripts', 'python.exe')
    : path.join(backend, '.venv', 'bin', 'python')

const backendTests = full
  ? ['tests']
  : [
      'tests/test_v2_t04_reference_analysis.py',
      'tests/test_v2_t05_requirements.py',
      'tests/test_v3_p1_t06_gate.py',
      'tests/test_v2_t49_legacy_review.py',
      'tests/test_role_dag.py',
      'tests/test_role_chain_consolidation.py',
      'tests/test_role_chain_migration.py',
      'tests/test_issue_118_t06.py::test_budget_survives_restart_and_exhaustion_keeps_last_candidate',
      'tests/test_pen_chain_remediation.py',
      'tests/test_gate_free_workflow.py',
      'tests/test_collaborative_revisions.py',
      'tests/test_conversation_alignment.py',
      'tests/test_delivery_publication_race.py',
      'tests/test_editable_prompt_confirmation.py',
      'tests/test_clarification_contract_compat.py',
      'tests/test_openai_chat_adapter.py',
      'tests/test_llm_thinking_mode.py',
      'tests/test_markdown_handoff.py',
      'tests/test_prompt_content.py',
    ]

function checkDependencyGraph() {
  const installations = [
    path.join(root, 'node_modules', 'vue-router'),
    path.join(root, 'main', 'node_modules', 'vue-router'),
  ]
    .filter((candidate) => existsSync(candidate))
    .map((candidate) => realpathSync(candidate))

  const unique = [...new Set(installations)]
  if (unique.length !== 1) {
    throw new Error(
      `expected one vue-router installation, found ${unique.length}: ${unique.join(', ')}`,
    )
  }
  console.log(`[check] dependency graph: ${unique[0]}`)
}

const stages = [
  {
    label: 'dependency graph',
    display: 'one physical vue-router installation',
    run: checkDependencyGraph,
  },
  {
    label: 'typecheck',
    command: npm,
    args: npmArgs(['run', 'typecheck']),
    display: 'npm run typecheck',
    cwd: root,
  },
  {
    label: 'frontend tests',
    command: npm,
    args: npmArgs(['test']),
    display: 'npm test',
    cwd: root,
  },
  {
    label: 'backend lint',
    command: python,
    args: ['-m', 'ruff', 'check', '../main/backend/app', '--ignore', 'E501'],
    cwd: backend,
  },
  {
    label: 'backend tests',
    command: python,
    args: ['-m', 'pytest', '-q', ...backendTests],
    cwd: backend,
  },
]

function describe(stage) {
  if (stage.display) return stage.display
  if (stage.run) return stage.label
  return `${stage.command} ${stage.args.join(' ')}`
}

if (dryRun) {
  console.log('[check] plan')
  stages.forEach((stage, index) => {
    console.log(`${index + 1}. ${stage.label}: ${describe(stage)}`)
  })
  process.exit(0)
}

if (!existsSync(python)) {
  console.error(`[check] backend python not found: ${python}`)
  process.exit(1)
}

for (const stage of stages) {
  console.log(`\n[check] ${stage.label}`)
  if (stage.run) {
    stage.run()
    continue
  }
  const result = spawnSync(stage.command, stage.args, {
    cwd: stage.cwd,
    stdio: 'inherit',
  })
  if (result.error) {
    console.error(result.error)
    process.exit(1)
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

console.log('\n[check] all stages passed')
