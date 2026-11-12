# T17 (#103) single-chat demand cards, one authorization and original task card

## Baseline and scope

- Baseline: `da7cdef` (integration/v3 after T16)
- Implementation: `33296b5` on `ticket/103`
- Scope: requirement card and task card in the single chat stream, hard/soft constraint layering, one valid primary confirmation entry and duplicate-free polling.

The implementation stays in `main/src/components/workflow/WorkflowPanel.vue`. The panel is rendered inside `ChatPage`'s `.chat-stream`; T17 turns the server `task_state` into a streamed task card and renders the confirmed constraint layers in the requirement card. It does not add a second authorization path: the primary button still comes from the server `available_actions` and the existing confirmation state.

## Behaviour

| Situation | Result |
| --- | --- |
| Pending approved requirement card | Hard constraints under 必须满足（硬约束）, soft preferences under 偏好（软约束，可调整） |
| One valid pending card | Exactly one 确认以上需求并生成图片 button |
| Already confirmed card | No duplicate primary confirmation button |
| Same workflow response refreshed | One requirement card, one task card, one button; zero writes |
| Server task state with a safe reason | Task card shows the safe reason and progress, without the legacy current-question heading |
| Delivery facts | Delivery entry remains keyed by the stable delivery id |

## Commands and results

```text
cd .worktrees/t103/main
D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.json
# exit 0, no diagnostics

cd .worktrees/t103
D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts tests/t103-single-chat-cards.spec.ts
# 1 file passed, 4 tests passed in 3.18s

D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts
# 41 files passed, 193 tests passed in 24.19s
```

## Acceptance mapping

- **FE3-AC-02**: pass for one chat message stream and no permanent separate workflow column.
- **FE3-AC-03**: pass for the single current valid card and the absence of a duplicate confirmation entry once confirmed.
- **FE3-AC-04**: inherited T74/T13 and backend atomic-acceptance coverage remains green; T17 adds no write path.
- **FE3-AC-13**: task card consumes the server safe reason/progress/actions without fake percentages, auto retry or unsupported cancel.
- **FE3-AC-15**: existing recovery/action suites remain green; buttons come from server actions and the task card uses the server reason.
- **WG3-AC-10**: safe reason/progress/constraint strength projection passes in jsdom; real browser/provider observation remains release scope.

## Residuals

- This is jsdom evidence with HTTP doubles, not a real browser or provider run.
- Responsive, keyboard and soft-keyboard behavior remain T22/T25 scope.
- Backend task-state production and hard/soft constraint adjudication are covered by the T11/T13 backend evidence.
