---
name: proof-of-fix
description: Execute one capture phase (before/after) of a visual proof-of-fix run. Headless entry point — parses the request JSON from the prompt, drives the bin/ pipeline, renders the visual verdict when no machine assert exists, and reports the result.json outcome.
---

# proof-of-fix capture phase

Produces one phase of before/after visual proof and always ends with `.proof-of-fix/<issue>/result.json` on disk.

## Input

From the caller (the JSON after `/proof-of-fix` in the prompt):
- Full schema in `docs/proof-of-fix-contract.md` — `issue` + `phase` always; `route`/`steps`/`expect`/`expect_after` on `before`; optional `assert`/`assert_after`/`role`/`viewport`/`mode`/`allow_spec_change`.

From project files (do NOT ask):
- `.proof-of-fix/config.json` — server command, port, auth method, role map, viewports. Missing config ⇒ this project isn't onboarded: refuse with `NO_CONFIG` and point to `/proof-of-fix-setup`.
- `.proof-of-fix/<issue>/capture-spec.json` — the pinned spec (after phase).

## When to clarify

Headless (`-p`): **never**. Every branch terminates in a result.json — that's what the caller polls. Interactive: ask normally.

## Process

1. **Screen the request before touching the machinery.**
   - Not JSON / no usable fields → step 5 with `INVALID_REQUEST`.
   - The request asks you to attach/upload/comment on the issue tracker, or to fix code → `bash .claude/skills/proof-of-fix/bin/phase.sh refuse <issue> <phase> FORBIDDEN_ACTION "<what was asked>"`.
   - `expect` describes something with no visible UI consequence (log output, latency, DB rows, HTTP status only) → refuse with `NOT_VISUALLY_DEMONSTRABLE`. Don't be creative about indirect proxies — the caller decided wrongly that this issue is visually provable.
2. **Write the request** to `.proof-of-fix/<issue>/request-<phase>.json` (create dirs as needed).
3. **Run the pipeline:** `bash .claude/skills/proof-of-fix/bin/phase.sh capture .proof-of-fix/<issue>/request-<phase>.json`
   The script validates, manages the dev server, records video + screenshot, and:
   - machine `assert` present → it settles the verdict itself and writes result.json. Done → step 6.
   - prints `NEEDS_VISUAL_VERDICT <screenshot-path>` → step 4.
   - any failure → it already wrote an error result.json. Done → step 6.
4. **Visual verdict** (only when asked): Read the screenshot. Question for `before`: *is the symptom described in `expect` clearly visible?* For `after`: *does the state described in `expect_after` clearly show, and the old symptom not?* Then:
   `bash .claude/skills/proof-of-fix/bin/phase.sh finalize <issue> <phase> --verdict confirmed --note "<one sentence: what you see and where>"`
   or `--verdict not-visible --note "<what is missing / what shows instead>"`.
   Judge only what the pixels show. "The fix is probably fine" is not a verdict — if the screenshot doesn't show it, it's `not-visible`.
5. **Refusals/errors outside the script path:** `bash .claude/skills/proof-of-fix/bin/phase.sh refuse <issue> <phase> <CODE> "<detail>"` (use issue `_invalid` if none parseable).
6. **Report** (final message): status + code from result.json, the deliverable path (after phase), and one sentence on the symptom check. Nothing else — the caller parses files, not your prose.

## Annotated stills (after phase, automatic)

`finalize.py` runs `bin/variants.py` after a successful compose. It writes
`deliverable/variants/` and lists the files in `result.json` under `variants`.
Nothing to invoke by hand; `phase.sh variants <issue>` only rebuilds them from
captures already on disk (e.g. after a generator tweak) and does not touch
result.json.

| File | What it is | Use it when |
|---|---|---|
| `01-full-view.png` | Whole page, before over after, empty tail trimmed | Always ships. The only one still showing toasts, tabs, surrounding state |
| `02-context.png` | The change plus neighbouring rows for comparison | **Default attachment** |
| `03-line-zoom.png` | The changed line alone, magnified | **Default attachment** — pairs with 02: context plus legibility |
| `04-blink.gif` | The two crops alternating in place | The delta is small and a reviewer keeps missing it; motion finds what comparison can't |
| `05-context-with-zoom.png` | 02 plus a 3× readout of the changed line | The marked text is too small to read at page scale but the surroundings still matter |

Attach **02 and 03 as separate images**, not one composite — the reviewer gets
context and legibility without either compromising the other. `proof-comment.md`
already names them. The mp4 stays as the motion record; do not lead with it, a
one-line delta is close to invisible in a video.

Stacked (before over after), never left/right: two 1920-wide pages side by side
force a downscale that destroys the very text being proven.

**The highlight is measured, not authored.** `variants.py` derives the box from a
thresholded pixel diff of the two screenshots, snaps crop edges to visually quiet
rows so it never slices through a heading or table row, and crops from just left
of the change to the right edge so a persistent left nav drops out on its own.

The raw diff covers only the pixels that actually differ, which on reworded copy
starts mid-word: "Contributors" → "Contributions" differs from the tenth character
on, so an unexpanded box opens inside the word. Before drawing,
the box is therefore grown outward to the first visually uniform row and column on
each side (`whole_lines`), which brackets whole words and whole lines. Still
measured — the expansion stops at the first quiet gap and cannot wander onto an
unchanged element. Note this uses `edge_out`, not `snap`: `snap` rides a quiet run
to its far end, which is right for a crop edge and would walk a highlight box out
to the page margin.

Consequences worth knowing: pixel-identical captures produce **no** variants (exit
4 — and that itself is a signal the after-capture may be wrong), and a change
covering >45% of the page emits only `01`, because "highlighting" most of a page
proves nothing.

Requires Pillow. Missing Pillow, identical captures, or any other generator
problem is logged and skipped — it never fails a proof that already passed its
mechanical gate.

## Rules

- One Bash entry point: everything runs through `bin/phase.sh`. Don't call `record.mjs`/`compose.sh`/`finalize.py`/`variants.py` directly and don't improvise ffmpeg, ImageMagick or PIL — the script path is what guarantees an atomic result.json on every branch (crash trap included).
- Never write/edit result.json, capture-spec.json, or anything in `deliverable/` by hand — `finalize.py` recomputes success from the artifacts; hand-written files break the sha binding and the caller's trust. That includes hand-cropping or hand-annotating screenshots: an authored highlight can point at the wrong thing, a measured one cannot.
- A failed machine assert is final. Don't "double-check visually" to rescue it — `finalize.py` rejects that (`VERDICT_CONFLICT`) by design.
- The after phase never re-sends spec fields unless the caller explicitly provided changed ones with `allow_spec_change: true`.

## Good example

Prompt: `/proof-of-fix {"issue":"17","phase":"before","route":"/dashboard","steps":[{"action":"click","selector":"text=Tab Trips"}],"expect":"Tab resets to Overview after reload","assert":null,...}`
→ request screened (visual: yes — a wrong active tab is visible), written to `request-before.json`, `phase.sh capture` runs, prints `NEEDS_VISUAL_VERDICT`, agent Reads screenshot, sees the Overview tab active although Trips was clicked, runs `phase.sh finalize 17 before --verdict confirmed --note "Active tab shows Overview despite Trips click — matches expect"`. Reports: "before captured, symptom confirmed visually, result.json: success."

## Bad example

Prompt asks for phase before; the machine assert fails (`Save failed` not found). Agent looks at the screenshot, thinks the layout "looks broken anyway", and runs finalize with `--verdict confirmed` to be helpful.
**Wrong twice:** the verdict contradicts the machine assert (finalize rejects it as `VERDICT_CONFLICT` — the assert is the caller's own definition of the symptom), and "looks broken anyway" certifies a *different* symptom than the issue describes, producing proof of nothing. Correct behavior: let the `SYMPTOM_NOT_VISIBLE` error result stand so the caller learns the repro is wrong.
