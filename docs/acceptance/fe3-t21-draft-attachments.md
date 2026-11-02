# T21 (#107) image/text input, Chinese IME and draft lifecycle

## Baseline and scope

- Baseline: `bb400dc` (integration/v3 after T20)
- Implementation: `e5fb03e` on `ticket/107`
- Scope: the single bottom composer, IME/keyboard rules, reference picker/paste/drop, attachment preview, project-bound upload isolation, same-project route preservation and unsaved/lifecycle cleanup.

## Behaviour

| Situation | Result |
| --- | --- |
| Enter / Ctrl+Enter | Sends through the same composer action |
| Shift+Enter | Inserts a newline, does not send |
| Chinese IME composition + Enter | Composition is not interrupted and Enter does not send; real Edge check records `compositionstart` and `keydown` with `isComposing=true` |
| Duplicate Enter while sending | Only one message POST is issued |
| Reference picker / paste / drop | One PNG/JPEG is prepared with a temporary preview; replace/remove use the existing server revision |
| Ordinary text paste | Not prevented or swallowed |
| Text/non-image drop | Does not navigate and does not start an upload |
| Upload delayed past project switch | The late response is ignored; project B receives no reference upload and no pending preview |
| Same project chat ↔ results | Draft and pending attachment preparation survive the child route change |
| Switch/leave with attachment only | UnsavedChangesDialog lists 尚未上传完成的参考图; cancel keeps it, confirm discards |
| Account switch / cross-tab invalidation | Draft, project state and temporary reference image are cleared |
| Refresh | No unsent draft or temporary image is promised across a reload |
| Paste/drop/navigation | No message POST, generation POST or model call |

## Commands and results

```text
cd .worktrees/t107
npx vitest run tests/t107-draft-attachments.spec.ts
# 1 file passed, 6 tests passed in 4.26s

npx vitest run
# 45 files passed, 214 tests passed in 28.10s

npm run typecheck
# exit 0, no diagnostics
```

Real Edge browser check (playwright-core 1.64.1, system Edge, mocked `/api`):

```text
imeCompositionEvents: ["compositionstart:plain"]
imeEnterDidNotSend: true
clipboardTextPaste: "真实剪贴板文字"
clipboardImageWritten: true
pendingVisibleAfterImagePaste: true
pendingPreviewVisible: true
referenceUploadRequests: 1
dropTextNoNavigation: true
referenceUploadRequestsAfterTextDrop: 1
attachmentWarningVisible: true
pendingAfterCancel: true
crossTabDraftCleared: true
crossTabPendingCleared: true
duplicateEnterMessages: 1
unknownRoutes: []
ok: true
```

## Acceptance mapping

- **FE3-AC-11**: pass. The single composer uses Enter/Ctrl/Cmd to send, Shift+Enter for newline, ignores Enter during IME composition and deduplicates an in-flight send. Picker, paste and drop all prepare one reference with preview; normal text paste and non-image drop remain ordinary input.
- **FE3-AC-12**: pass. Same-project chat/results preserves the page draft and pending attachment; project switch and leaving warn for either unsent text or an unfinished reference image and clear only after confirmed discard. Session lifecycle and a real cross-tab BroadcastChannel invalidation clear drafts, project state and temporary images. No local business database or cross-device sync is added.

## HTTP and call counts

The focused suite and browser check use the existing HTTP boundary with mocked responses. The browser check saw one `POST /api/projects/{A}/reference`, zero `POST /api/projects/{B}/reference`, one `POST /api/projects/{A}/messages` from the duplicate-Enter probe, and zero generation submissions. No provider or image service was contacted.

## Residuals

- The real-browser check uses mocked API responses; live backend/provider behavior is outside this evidence.
- Clipboard image paste depends on browser clipboard permission support.
- Drag/drop is a real browser `DataTransfer` event, not an OS file drag.
- Firefox and Safari were not run; Edge Chromium is the only real browser here.
- No backend suite was rerun; T21 changes only frontend state and presentation.
