# T18 (#104) single composer edit and new-version loop

## Baseline and scope

- Baseline: `c799242` (integration/v3 after T17)
- Implementation: `7d73c62` on `ticket/104`
- Scope: all post-delivery substantive edits move to the original single chat composer, using the server `request_new_version` action; the separate feedback form is removed; results back-to-modify is navigation-only.

The implementation extends `ConversationPanel.vue` with a revision baseline derived from the current server delivery and the server action list. `WorkflowPanel.vue` no longer renders the old `workflow-feedback` section. `useWorkspace` gains a guarded detail refresh that applies only when the project context and session revision still match.

## Behaviour

| Situation | Result |
| --- | --- |
| Published current delivery | Composer shows `当前交付为第 N 版（filename），尺寸 …` and sends the server `request_new_version` action with the current project revision |
| Approved but unpublished current delivery | The same composer path is used; no downloadable old image is offered as a fake result |
| No adjustable current delivery | No revision context or revision action is shown |
| History preview selected | Edit baseline remains the current delivery; selecting history does not change the revision used by the next send |
| Send failure | Draft is preserved, the error is shown, and no success/new-version state is fabricated |
| Repeated send while in flight | Only one action POST is issued |
| Results page | No composer or feedback form; 返回对话修改 navigates to `project-chat#composer` and focuses the labeled input with zero writes |

## Commands and results

```text
cd .worktrees/t104/main
D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.json
# exit 0, no diagnostics

cd .worktrees/t104
D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts tests/t104-single-composer-edit.spec.ts tests/t75-u3-delivery.spec.ts
# 2 files passed, 12 tests passed in 4.16s

D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts
# 42 files passed, 199 tests passed in 24.27s
```

## Acceptance mapping

- **FE3-AC-08**: pass — results body and folds contain no edit input, feedback form, composer or editable canvas; back-to-modify only navigates and focuses, with no message/action/model write.
- **FE3-AC-09**: pass — a real send of a substantive edit calls the server new-version action and creates the next revision only after acceptance; success enters the new U1 review state; history preview does not change the edit baseline; old image is never automatically used as reference.
- **WG3-AC-10**: pass for this surface — only the server available action is dispatched, the original conversation remains the sole input, and T17's safe task-card projection remains the status surface.

## HTTP side-effect observations

The T18 suite asserts the action POST body exactly:

```json
{"expected_revision": 3, "text": "…", "payload": {}}
```

It also asserts zero `POST /api/projects/{id}/messages` calls, one action POST per accepted send, and zero action POSTs when only navigating back from results. No model or image provider is called.

## Residuals

- jsdom with HTTP doubles, not a real browser/provider run; real viewport, IME and soft keyboard remain T21/T22/T25 scope.
- The approved-but-unpublished view is a test double; server action availability remains backend-owned.
- Backend revision, independent review and confirmation invariants are covered by existing workflow suites, not duplicated here.
