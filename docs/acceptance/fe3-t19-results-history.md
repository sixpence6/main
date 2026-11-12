# T19 (#105) read-only results, history, prompt and original download

## Baseline and scope

- Baseline: `c90287f` (integration/v3 after T18)
- Implementation: `cb2debd` on `ticket/105`
- Scope: the results route is read-only. It shows the requested published delivery at original aspect, its own metadata and download, a folded history list and that version's real approved prompt. Missing or invalid versions never fall back to a cache or another version.

The backend version projection now includes `width` and `height` only when the delivery asset is downloadable. `ResultsPage.vue` uses those values for the current and history views and keeps the existing asset route as the only download path.

## Behaviour

| Situation | Result |
| --- | --- |
| Current published result | Large image uses `object-fit: contain`; metadata shows requirement revision, actual size and generation time; download points at that asset |
| Selected old version | URL keeps only `?version=<run_id>`; image, size, time, prompt and download all come from that version; note says the edit baseline is the current delivery |
| History list | `<details>` starts closed; expanding shows sequence, requirement revision and real size; current entry is marked `aria-current` |
| Approved prompt | Folded by default; displays the selected version's own positive/negative prompt |
| Missing prompt | Explicit unavailable message; current prompt is never substituted |
| Missing/unpublished/revoked/expired/invalid version | Safe `结果不可用` state with no image; accessible history remains available |
| Back actions | Navigation only; no message, version, confirmation or model call |
| Results body/folds | No composer, feedback form or editable canvas |

## Commands and results

```text
cd .worktrees/t105
npx vitest run tests/t105-results-history.spec.ts
# 1 file passed, 4 tests passed in 3.12s

npx vitest run
# 43 files passed, 203 tests passed in 25.03s

npm run typecheck
# exit 0, no diagnostics

cd .worktrees/t105/backend
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q \
  tests/test_t75_delivery_history.py tests/test_t75_image_size_gate.py \
  tests/test_v3_p1_t05_image_delivery.py tests/test_v3_p1_t10_execution_snapshot.py
# 17 tests passed in 12.25s

D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m ruff check \
  main/backend/app/workflow/schemas_workflow.py \
  main/backend/app/workflow/state.py \
  backend/tests/test_t75_delivery_history.py
# All checks passed
```

## Acceptance mapping

- **FE3-AC-07**: pass. The large image and download are bound to the requested version. Only published, downloadable versions render; a missing version shows a safe state and no cached/other-version image. T75 verifies preview and download bytes are identical and that a rejected asset returns `ASSET_REJECTED` with no asset id, filename or dimensions.
- **FE3-AC-08**: pass. Results body and all folds have no input, feedback form or editable canvas. Both back actions only `router.push`; 返回对话修改 targets `#composer` for the original input focus, and the frontend recorder observes zero writes and zero workflow action POSTs.
- **WG3-AC-12**: pass for the T19 results surface. Image availability comes from the server delivery/version state, not a report pass; rejected/blocked versions expose no image or download. The normal/recovery approval boundary is inherited from the T75/T12 backend suites and is not reimplemented in the browser.

## HTTP, bytes and call counts

`backend/tests/test_t75_delivery_history.py` exercises real ASGI HTTP with a temporary SQLite database. It checks that `/api/assets/{id}` preview and `?download=true` return equal non-empty bytes, that a rejected asset is denied with `ASSET_REJECTED`, that the version snapshot is unchanged after refresh, and that role/model and visual submit counters do not increase on history refresh or new-version feedback.

The T19 frontend tests use HTTP doubles and record zero writes and zero `POST .../workflow/actions` calls while selecting history, opening history, downloading and navigating back. No provider or image service is contacted in this evidence.

## Residuals

- jsdom harness, not a real browser: real viewport behavior, cross-browser `<details>` accessibility and focus order remain T22/T25 scope.
- Backend asset authorization and file checks are existing product behavior re-verified by T75, not newly implemented here.
- The history collection includes the current published version in its count and list.
- A legacy frontend version payload without dimensions falls back to the loaded project generation for the current asset; backend downloadable versions always include dimensions after this change.
