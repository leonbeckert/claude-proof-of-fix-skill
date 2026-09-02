# proof-of-fix — Caller Contract

Read this if you are an agent (or human) who wants before/after visual proof of an issue fix. This is the complete machine contract; nothing else is required reading.

## Invocation

Run **in the target project root** (the directory containing `.proof-of-fix/config.json`):

```bash
# 1) BEFORE applying the fix — capture the broken state:
claude --agent proof-of-fix -p '/proof-of-fix {"issue":"42","phase":"before","route":"/dashboard?tab=users","role":"admin","steps":[{"action":"click","selector":"text=Save"},{"action":"waitMs","ms":500}],"expect":"Red error banner: Save failed (500)","assert":{"type":"text","text":"Save failed"},"expect_after":"Green confirmation: Saved"}' </dev/null

# 2) Apply your fix, then capture the fixed state — the stored spec is replayed, send only:
claude --agent proof-of-fix -p '/proof-of-fix {"issue":"42","phase":"after"}' </dev/null
```

`</dev/null` is mandatory: `claude -p` also reads stdin, and inherited stdin corrupts headless runs.

The `claude -p` call is synchronous — when it exits, read `.proof-of-fix/<issue>/result.json`. If the process exited and no `result.json` exists for that phase, treat the run as **CRASHED** and retry or escalate; do not wait on the file.

## Request fields

| Field | Required | Notes |
|---|---|---|
| `issue` | always | ID string `[A-Za-z0-9._-]+` — names `.proof-of-fix/<issue>/` and the proof comment |
| `phase` | always | `"before"` or `"after"` |
| `route` | before | Path (`/dashboard?tab=users`) or absolute localhost URL |
| `steps` | before | Interaction list, may be `[]` for static issues. Actions: `goto` (`path`), `click`/`hover` (`selector`), `fill` (`selector`,`value`), `press` (`key`), `select` (`selector`,`value`), `waitFor` (`selector`), `waitMs` (`ms` ≤ 10000). Max 30 steps. Selectors are Playwright selectors (`text=`, `#id`, `role=`…) |
| `expect` | before | Human-readable description of the **visible** symptom. If the symptom is not visually observable, don't call this agent — you'll get `NOT_VISUALLY_DEMONSTRABLE` |
| `expect_after` | before | What the fixed state must visibly show |
| `assert` | optional | Machine check for the symptom: `{"type":"text","text":"..."}` or `{"type":"visible"\|"hidden","selector":"..."}`. Strongly recommended — without it the agent judges the screenshot visually |
| `assert_after` | optional | Same shape, checked in the after phase against the fixed state |
| `role` | optional | Key from the project config's role map (seed users only). Omit for `default_role` |
| `viewport` | optional | `"desktop"` (default, 1920×1080) \| `"mobile"` (375×667) \| `{"width":W,"height":H}` |
| `mode` | optional | `"auto"` (default) \| `"video"` \| `"still"`. Auto: any interaction step ⇒ video, pure navigation ⇒ still |
| `allow_spec_change` | after only | Set `true` (and resend the changed fields) when the fix legitimately changed the UI path. Result then carries `spec_changed: true` |

**Data setup is your job.** The capture runs against whatever the local dev server + seed data show. If the symptom needs specific records, create them before invoking (or encode creation in `steps`).

## The comparability rule

The `before` phase persists route/steps/viewport/role as `capture-spec.json`. The `after` phase **replays that stored spec** — you send only `{issue, phase}`. Sending different values without `allow_spec_change` is refused as `SPEC_MISMATCH`. This is deliberate: a before/after pair captured under different conditions proves nothing.

## Outputs

```
.proof-of-fix/<issue>/
  result.json           ← poll THIS. Written atomically, always last, on every outcome
  capture-spec.json     before/  after/        (screenshot.png, video.webm, meta.json each)
  deliverable/          before-after.png|mp4 + proof-comment.md   (after phase)
    variants/           annotated stills — 01-full-view, 02-context, 03-line-zoom,
                        04-blink.gif, 05-context-with-zoom   (after phase)
  history/<timestamp>/  previous runs, plus the capture-spec.json they ran under
                        if a later phase replaced it (last 5 kept)
```

**Attach `02-context.png` and `03-line-zoom.png` as two separate images.** The
highlight in them is the measured pixel diff between the two captures, not a
hand-drawn box. The mp4 remains the motion record — do not lead with it, a
one-line change is close to invisible in a video. `result.json` lists
every generated variant under `variants`. Fewer variants than expected is
informative: pixel-identical captures produce none at all, and a change covering
most of the page produces only `01-full-view.png`.

`result.json`:

```json
{
  "agent": "proof-of-fix", "issue": "42", "phase": "after",
  "status": "success",             // success | error | refused
  "code": null,                    // machine-readable code when not success
  "detail": null,
  "symptom_check": {"method": "machine", "outcome": "confirmed", "note": "..."},
  "spec_changed": false, "spec_diff": null,
  "artifacts": [{"path": "after/screenshot.png", "sha256": "..."}],
  "deliverable": "deliverable/before-after.mp4",
  "variants": ["deliverable/variants/02-context.png", "..."],
  "proof_comment": "deliverable/proof-comment.md",
  "timestamp": "2026-08-14T09:00:00Z"
}
```

`status: "success"` is only ever written by the mechanical gate (`finalize.py`) after recomputing artifact hashes, video codec/duration, and verdict rules.

**After a human reviews the auto-opened deliverable and approves:** attach `deliverable/before-after.*` and the annotated stills to the issue yourself, and use `deliverable/proof-comment.md` as the comment body. proof-of-fix will not and cannot do this.

## Error codes

| Code | Meaning / your reaction |
|---|---|
| `INVALID_REQUEST` | Missing/malformed fields (listed in `detail`) — fix the request |
| `NOT_VISUALLY_DEMONSTRABLE` | The described symptom isn't visual — skip visual proof for this issue |
| `SYMPTOM_NOT_VISIBLE` | Before-capture didn't show the symptom — your route/steps/data are wrong, or the bug doesn't reproduce |
| `FIX_NOT_VISIBLE` | After-capture still shows the symptom / not the expected state — the fix may not work |
| `NO_BEFORE` | `after` without a stored before-run for this issue |
| `SPEC_MISMATCH` | You changed route/steps/viewport/role without `allow_spec_change` |
| `NON_LOCAL_TARGET` | Non-localhost origin — captures run against local dev + seed data only |
| `UNKNOWN_ROLE` | Role not in the project config's seed-user map |
| `SERVER_UNREACHABLE` | Dev server didn't become healthy — check `.proof-of-fix/server.log` |
| `NO_CONFIG` | Project not onboarded — a human must run `/proof-of-fix-setup` there once |
| `AUTH_FAILED` | Login (HMAC endpoint or form) failed — check config auth + server env |
| `STEP_FAILED` | An interaction step errored (selector not found, timeout) — `detail` names the step |
| `FORBIDDEN_ACTION` | You asked the agent to attach/upload/fix — that is a human's job, not the capture's |
| `VERDICT_CONFLICT` | Internal guard: a visual verdict tried to override a failed machine assert |
| `CRASH` | Pipeline aborted unexpectedly — see `detail`, check `server.log`, retry once |

An unparsable request (no usable `issue`) writes `.proof-of-fix/_invalid/result.json` instead.
