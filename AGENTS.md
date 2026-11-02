### rule

main 文件夹为项目本体，任何与项目无关的文件、脚手架、测试代码都不应该放在 main 文件夹中。后端依赖、代码、配置、数据库等要放入文件夹

## Agent skills

### Issue tracker

Issues live as GitHub issues in `sixpence6/main`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### v3 architecture

For v3 implementation, cleanup, or release work, read `docs/architecture/hld_v3.md`: R1 → R2 → R3 is serial and each stage has an acceptance gate. Read `docs/architecture/v3-refactor-plan.md` for candidate rationale only; neither document is evidence that v3 has shipped.

For gate adjudication, prompt repair, execution snapshots, or chat/result handoffs, read `docs/architecture/workflow-gates-v3.md`; UI behavior stays in `docs/architecture/frontend-design-v3.md`. Audit findings are historical evidence: verify the current baseline before marking a fix or acceptance complete.
