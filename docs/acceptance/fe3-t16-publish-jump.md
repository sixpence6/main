# T16 (#102) one publish jump and late-callback isolation

## Baseline and scope

- Baseline: `6015a65` (integration/v3 after T15)
- Implementation: `e3e0f74` on `ticket/102`
- Scope: active current-chat publish jump, stable delivery dedupe, first-load/history/back baselines and late-response isolation.

The implementation stays in `main/src/pages/ChatPage.vue`. The existing workflow controller already guards project, session and request sequence; T16 adds project-id binding, login-lifecycle reset and a stable delivery-id dedupe before the single results navigation.

## Behaviour

| Situation | Result |
| --- | --- |
| First load already has a published current result | Recorded as baseline; no jump |
| Active S10 chat observes delivery become published | One push to `project-results` |
| Same delivery is observed again | No second push |
| Back to the same chat | No re-jump |
| Direct results or explicit history route | Stays on that route |
| Project switch with late old workflow response | Current project route is kept |
| Login lifecycle change during active chat | Baseline is cleared; no old-session jump or route steal |

All transition and negative cases assert zero non-GET writes. No message, workflow action, confirmation or model call is produced by navigation.

## Commands and results

```text
cd .worktrees/t102/main
D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.json
# exit 0, no diagnostics

cd .worktrees/t102
D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts tests/t102-publish-jump.spec.ts
# 1 file passed, 6 tests passed in 3.27s

D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts
# 40 files passed, 189 tests passed in 23.80s
```

## Acceptance mapping

- **FE3-AC-05**: pass for the active current chat, first-load baseline, single transition jump, history/direct-route non-jump and late project/session isolation.
- **WG3-AC-11**: frontend route/response identity passes. Real browser back-forward cache, cookie persistence and provider timing remain release scope.

## Residuals

- This is jsdom evidence with a fake clock and HTTP doubles, not a real browser or provider run.
- Background generation continuation is represented by read-only workflow polling; the backend permission/budget suites are unchanged and green.
- Responsive and soft-keyboard behavior remain T22/T25 scope.
