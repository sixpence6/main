# T15 (#101) shared workspace shell, chat and results routes

## Baseline and scope

- Baseline: `bf24d9c` (integration/v3 after T13)
- Implementation: `cd81248` on `ticket/101`
- Scope: shared shell for empty chat, project chat, current results and explicit history; safe version states; navigation-only return actions; per-project page draft; account cleanup.
- The project remains a single Vue app with real Vue Router and the existing workspace harness. All HTTP is a test double; no model, image or provider call was made.

## Implemented routes and behaviour

| Route | Behaviour |
| --- | --- |
| `/` | Empty chat workspace, composer available, no project is created until an explicit send |
| `/projects/:projectId/chat` | Project chat in the same shell, one primary conversation body |
| `/projects/:projectId/results` | Current published result, no composer or feedback form |
| `/projects/:projectId/results?version=<run_id>` | Explicit history only when that version is published, downloadable and has an asset |

The results page displays the selected version's image, revision, actual dimensions, published time and download link. Missing, unpublished, revoked and non-downloadable versions show `结果不可用` and never substitute the current image or a cached image.

`返回对话` returns to the project chat. `返回对话修改` additionally focuses the labelled composer. Neither action sends a message, creates a revision, confirms a requirement or calls a model.

## Commands and results

```text
cd .worktrees/t101/main
D:/Desktop/新建文件夹/node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.json
# exit 0, no diagnostics

cd .worktrees/t101
D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts tests/t101-shared-shell.spec.ts
# 1 file passed, 10 tests passed in 3.72s

D:/Desktop/新建文件夹/node_modules/.bin/vitest.cmd run --config vitest.config.ts
# 39 files passed, 183 tests passed in 23.72s

git diff --check
# clean
```

The focused file covers: root empty chat with no project POST; shared shell and no composer in results; current image/download metadata; explicit history and its own prompt; missing/unpublished/revoked version safe states; return-to-modify navigation and focus with zero writes; same-project draft preservation across body switches without URL leakage; account lifecycle draft cleanup.

## Acceptance mapping

- **FE3-AC-06**: pass for shared-shell navigation and history selection; all observed writes are zero.
- **FE3-AC-07**: pass for exact current/history image and download binding plus safe no-fallback states.
- **FE3-AC-08**: pass for results without composer/feedback and navigation-only return actions.
- **FE3-AC-12**: frontend portion passes for same-project draft and account cleanup; the full T71 suite covers A→B→A and same-account relogin.
- **WG3-AC-11**: route identity and late-response isolation regressions pass in the full suite; browser cookie/cache evidence remains for release verification.

## Calls and counts

- model_text: 0
- model_vision: 0
- image_generation: 0
- provider network: 0
- The route/history/back/account tests assert zero POSTs and zero fetch writes.

## Residual unknowns

- jsdom is not a real browser: browser back/forward cache, real cookies, actual file download and OS focus remain release/browser evidence.
- Responsive layout and drawer/mobile keyboard behaviour remain T22/T25.
- No real provider or image service is involved; all counts are HTTP-double observations.
