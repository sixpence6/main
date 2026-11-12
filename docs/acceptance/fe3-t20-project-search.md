# T20 (#106) full owner project search and rename

## Baseline and scope

- Baseline: `87b289a` (integration/v3 after T19)
- Implementation: `989e8e7` on `ticket/106`
- Scope: the project sidebar uses the real server-side owner/title search before pagination, keeps late search and rename responses inside the account and query that started them, and persists metadata-only rename. No requirement, approval, intent, budget, slot or model call is changed.

## Behaviour

| Situation | Result |
| --- | --- |
| Search matches an unloaded page | The server receives `q` and `limit=20` before pagination; the matching project appears even when it was not in the local list |
| Empty query | Clearing the search returns the normal owner project list; no project is created |
| No result | The sidebar states that no title matches the query and offers the normal new-project entry |
| Query switch during a pending search | The old response is ignored and cannot replace the new query; the new request owns the list and cursor |
| Load more | Appends only the current query's next page, deduplicates by id and never mixes another query or account |
| First explicit send | The server derives the short deterministic local title, not a model title; an empty chat does not create a project |
| Explicit rename | PATCH persists the title and reopening the drawer shows it; a failed save keeps the old title and shows a safe error |
| Account switch while renaming | The late response is discarded; the new workspace does not display the old account's title |
| Current status | The list uses the current confirmed generation revision; an old success does not mark the current revision complete, and unknown stays pending |

## Commands and results

```text
cd .worktrees/t106
npx vitest run tests/t106-project-search.spec.ts
# 1 file passed, 5 tests passed in 4.86s

npx vitest run
# 44 files passed, 208 tests passed in 26.70s

npm run typecheck
# exit 0, no diagnostics

cd .worktrees/t106/backend
D:/Desktop/新建文件夹/backend/.venv/Scripts/python.exe -m pytest -q \
  tests/test_v3_p4_t14_project_search.py tests/test_t03_organize.py
# 20 tests passed in 21.24s
```

## Acceptance mapping

- **FE3-AC-10**: pass. The real App/router search test finds a project that exists only on the server-side page; rename is a single PATCH metadata write and survives drawer reopen; late responses are filtered by session and query; the backend T14 suite verifies owner isolation, validation, CSRF, metadata-only rename and current-status semantics. No model call is made by search, naming or rename.
- **Ticket account-preservation rule**: pass. Existing account-security, logout, personal-usage and authorization-entry tests remain in the full frontend suite; T20 adds no balance/recharge, full-text message search, project deletion or model selector.

## HTTP and call counts

The frontend tests use the existing `FetchController` seam and the full App/router. Search and clear issue only `GET /api/projects`; rename issues exactly one `PATCH /api/projects/{id}` with `{title}`. No provider or image service is contacted. The backend T14 tests use temporary SQLite and assert that rename does not change the requirement revision, confirmation digest, generation intent, revision budget or slots, and does not call a model.

## Residuals

- jsdom evidence, not a real browser: visual focus order, real viewport and cross-browser search/rename accessibility remain T22/T25 scope.
- The frontend uses HTTP doubles; real network latency and proxy behavior are outside this evidence.
- CSRF refusal and input validation are exercised by the backend T14 HTTP suite, not by a real browser form submission.
- The search is title-only and owner-scoped by the parent specification; message-body search and cross-owner search are intentionally absent.
- No full backend suite was rerun; affected T14/T03 suites and the full frontend suite were run.
