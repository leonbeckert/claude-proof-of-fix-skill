#!/usr/bin/env python3
"""The only writer of result.json. Computes success mechanically; never trusts prose.

Usage:
  finalize.py error  <issue> <phase> <CODE> <detail>
  finalize.py refuse <issue> <phase> <CODE> <detail>
  finalize.py verdict <issue> <phase> --verdict machine|confirmed|not-visible [--note "..."]

Verdict rules (the anti-fabrication core):
  machine    — accepted only if meta.json's assert was defined AND passed (recomputed here).
  confirmed  — visual verdict; accepted only if NO machine assert failed, and requires --note.
  not-visible — writes the SYMPTOM_NOT_VISIBLE / FIX_NOT_VISIBLE error result.
result.json is written atomically (tmp + os.replace), always last.
"""
import datetime, hashlib, json, os, re, subprocess, sys

ROOT = os.environ.get("POF_ROOT", os.getcwd())
ISSUE_RE = re.compile(r"[A-Za-z0-9._-]+")


def safe_issue(issue):
    # Defense in depth: finalize/refuse take `issue` straight from phase.sh argv,
    # which is untrusted. The regex forbids '/', so a traversal like ../../x can
    # never build a path outside .proof-of-fix/. Reject '.'/'..' components too.
    if not isinstance(issue, str) or not ISSUE_RE.fullmatch(issue) or issue in (".", ".."):
        return "_invalid"
    return issue


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def humanize_step(s):
    # The proof-comment is pasted into an issue comment for humans; render steps
    # as readable prose rather than raw JSON.
    a = s.get("action")
    if a in ("click", "hover"):
        return f"{a.capitalize()} `{s.get('selector')}`"
    if a == "fill":
        return f"Fill `{s.get('selector')}` with “{s.get('value')}”"
    if a == "select":
        return f"Select “{s.get('value')}” in `{s.get('selector')}`"
    if a == "press":
        return f"Press `{s.get('key')}`"
    if a == "goto":
        return f"Navigate to `{s.get('path')}`"
    if a == "waitFor":
        return f"Wait for `{s.get('selector')}`"
    if a == "waitMs":
        return f"Wait {s.get('ms')} ms"
    return f"`{json.dumps(s, ensure_ascii=False)}`"


def write_result(issue, payload):
    d = os.path.join(ROOT, ".proof-of-fix", issue)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".result.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"agent": "proof-of-fix", "issue": issue, "timestamp": now(), **payload},
                  f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, "result.json"))


def terminal(issue, phase, status, code, detail, extra=None):
    # Include any capture already on disk so a caller debugging e.g.
    # SYMPTOM_NOT_VISIBLE gets a hash-bound path to the screenshot to look at.
    arts = []
    for rel in (f"{phase}/screenshot.png", f"{phase}/video.webm"):
        p = os.path.join(ROOT, ".proof-of-fix", issue, rel)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            arts.append({"path": rel, "sha256": sha256(p)})
    write_result(issue, {"phase": phase, "status": status, "code": code, "detail": detail,
                         "symptom_check": None, "spec_changed": None, "spec_diff": None,
                         "artifacts": arts, "deliverable": None, "proof_comment": None,
                         **(extra or {})})
    print(f"{status.upper()} {code}: {detail}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(path, entry):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry,
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return out.stdout.strip()


def verdict_main(issue, phase, args):
    verdict = note = None
    it = iter(args)
    for a in it:
        if a == "--verdict":
            verdict = next(it, None)
        elif a == "--note":
            note = next(it, None)
    if verdict not in ("machine", "confirmed", "not-visible"):
        return terminal(issue, phase, "error", "CRASH", "finalize called without a valid --verdict")

    issue_dir = os.path.join(ROOT, ".proof-of-fix", issue)
    cfg = json.load(open(os.path.join(ROOT, ".proof-of-fix", "config.json")))
    spec = json.load(open(os.path.join(issue_dir, "capture-spec.json")))
    resolved = json.load(open(os.path.join(issue_dir, f"{phase}-resolved.json")))
    meta = json.load(open(os.path.join(issue_dir, phase, "meta.json")))
    a = meta.get("assert", {})

    if verdict == "not-visible":
        code = "SYMPTOM_NOT_VISIBLE" if phase == "before" else "FIX_NOT_VISIBLE"
        return terminal(issue, phase, "error", code, note or "visual verdict: expected state not shown",
                        {"symptom_check": {"method": "visual", "outcome": "failed", "note": note}})
    if a.get("defined") and not a.get("passed"):
        # A failed machine assert is the caller's own symptom definition failing — nothing overrides it.
        return terminal(issue, phase, "error", "VERDICT_CONFLICT",
                        f"verdict '{verdict}' rejected: machine assert failed ({a.get('detail')})")
    if verdict == "machine" and not a.get("defined"):
        return terminal(issue, phase, "error", "VERDICT_CONFLICT",
                        "verdict 'machine' rejected: no machine assert was defined")
    if verdict == "confirmed" and not a.get("defined") and not note:
        return terminal(issue, phase, "error", "CRASH", "visual verdict requires --note describing what is visible")
    method = "machine" if a.get("defined") else "visual"
    check = {"method": method, "outcome": "confirmed", "note": note or a.get("detail")}

    phase_dir = os.path.join(issue_dir, phase)
    has_video = spec["mode"] == "video"  # still mode records no video
    artifacts = [os.path.join(phase, "screenshot.png"), "capture-spec.json"]
    if has_video:
        artifacts.insert(1, os.path.join(phase, "video.webm"))
    for rel in artifacts:
        p = os.path.join(issue_dir, rel)
        if not (os.path.isfile(p) and os.path.getsize(p) > 0):
            return terminal(issue, phase, "error", "CRASH", f"required artifact missing/empty: {rel}")

    deliverable = comment_rel = None
    variants = []
    if phase == "after":
        mode = spec["mode"]
        r = subprocess.run(["bash", os.path.join(os.path.dirname(__file__), "compose.sh"), issue_dir, mode],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return terminal(issue, phase, "error", "CRASH", f"compose failed: {r.stderr.strip()[-400:]}")
        if mode == "still":
            deliverable = "deliverable/before-after.png"
            p = os.path.join(issue_dir, deliverable)
            ident = subprocess.run(["magick", "identify", "-format", "%w %h", p],
                                   capture_output=True, text=True).stdout.split()
            shot = subprocess.run(["magick", "identify", "-format", "%w %h",
                                   os.path.join(issue_dir, "after/screenshot.png")],
                                  capture_output=True, text=True).stdout.split()
            # Stacked, so the deliverable keeps one screenshot's width and roughly
            # doubles its height. Checking width alone would pass a broken compose
            # that emitted a single side; both dimensions have to be asserted.
            if not ident or not shot:
                return terminal(issue, phase, "error", "CRASH", "deliverable or screenshot not measurable")
            if abs(int(ident[0]) - int(shot[0])) > 4:
                return terminal(issue, phase, "error", "CRASH",
                                f"deliverable width {ident[0]} != screenshot width {shot[0]} — not stacked?")
            if int(ident[1]) < 2 * int(shot[1]):
                return terminal(issue, phase, "error", "CRASH",
                                f"deliverable height {ident[1]} < 2x screenshot height {shot[1]} — a side is missing")
        else:
            deliverable = "deliverable/before-after.mp4"
            p = os.path.join(issue_dir, deliverable)
            codec = probe(p, "stream=codec_name")
            dur = float(probe(p, "format=duration") or 0)
            d1 = float(probe(os.path.join(issue_dir, "before/video.webm"), "format=duration") or 0)
            d2 = float(probe(os.path.join(issue_dir, "after/video.webm"), "format=duration") or 0)
            if "h264" not in codec:
                return terminal(issue, phase, "error", "CRASH", f"deliverable codec '{codec}' is not h264")
            if dur + 0.05 < max(d1, d2):
                return terminal(issue, phase, "error", "CRASH",
                                f"deliverable duration {dur:.2f}s shorter than longest capture {max(d1, d2):.2f}s")
        artifacts += ["before/screenshot.png", deliverable]
        if has_video:
            artifacts.insert(-1, "before/video.webm")

        # Annotated stills. These are what gets attached to the issue — a reviewer
        # cannot spot a one-line delta in a stacked video, but can in a marked
        # still. Deliberately non-fatal: a presentation step must never fail a proof
        # that already passed its mechanical gate.
        vr = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "variants.py"),
                             issue_dir], capture_output=True, text=True)
        variants = [ln.strip() for ln in vr.stdout.splitlines() if ln.strip()] if vr.returncode == 0 else []
        if vr.returncode != 0:
            print(f"variants: {vr.stderr.strip()[-300:] or 'exit ' + str(vr.returncode)}", file=sys.stderr)
        artifacts += variants
        comment_rel = "deliverable/proof-comment.md"
        steps_txt = "\n".join(f"{i + 1}. {humanize_step(s)}" for i, s in enumerate(spec["steps"])) or "_(none — static view)_"
        before_check = json.load(open(os.path.join(issue_dir, "before-resolved.json")))
        before_meta = json.load(open(os.path.join(issue_dir, "before", "meta.json")))
        b_method = "machine" if before_meta.get("assert", {}).get("defined") else "visual"
        # A suggestion, not a decision: which still carries the change depends on how
        # big the change turned out, and only something that has looked at the images
        # can tell. SKILL.md makes that review a mandatory step of the after phase.
        picks = [v for v in variants if os.path.basename(v).startswith(("02-", "03-"))]
        attach_line = (
            "*Suggested attachments: " + ", ".join(f"`{os.path.basename(v)}`" for v in picks)
            + f" from `deliverable/variants/` — the marked region is the measured pixel diff. "
              f"Check them against the change before posting; on a wide change "
              f"`01-full-view.png` alone is usually the better evidence. "
              f"`{os.path.basename(deliverable)}` is the full motion record if anyone wants it.*"
            if picks else
            f"*(attach `{os.path.basename(deliverable)}` — top: before, bottom: after)*")
        with open(os.path.join(issue_dir, comment_rel), "w") as f:
            f.write(f"""## Proof of fix — issue #{issue}

Before/after captured under a pinned spec (viewport {spec['viewport']['width']}×{spec['viewport']['height']}, role `{spec.get('role') or '—'}`, route `{spec['route']}`) on {now()[:10]}.

- **Symptom (before):** {spec['expect']} — confirmed ({b_method})
- **Fixed state (after):** {spec['expect_after']} — confirmed ({method})
{'- **Note:** spec changed between phases: ' + json.dumps(resolved.get('spec_diff'), ensure_ascii=False) if resolved.get('spec_changed') else ''}

Reproduction steps:
{steps_txt}

{attach_line}
Generated by proof-of-fix; human-reviewed before attachment.
""")
        artifacts.append(comment_rel)

    write_result(issue, {
        "phase": phase, "status": "success", "code": None, "detail": None,
        "symptom_check": check,
        "spec_changed": resolved.get("spec_changed", False), "spec_diff": resolved.get("spec_diff"),
        "artifacts": [{"path": rel, "sha256": sha256(os.path.join(issue_dir, rel))} for rel in artifacts],
        "deliverable": deliverable, "proof_comment": comment_rel,
        "variants": variants if phase == "after" else [],
    })
    print(f"SUCCESS {phase} {deliverable or ''}".strip())

    if phase == "after" and cfg.get("auto_open", True) and not os.environ.get("POF_NO_OPEN"):
        # Open the marked still, not the video: reviewing is the whole reason this
        # opens at all, and the still is the one a human can actually judge.
        best = next((v for v in variants if os.path.basename(v).startswith("02-")), None) \
            or (variants[0] if variants else deliverable)
        subprocess.Popen(["open", os.path.join(issue_dir, best)])


def main():
    mode, issue, phase = sys.argv[1], safe_issue(sys.argv[2]), sys.argv[3]
    if phase not in ("before", "after"):
        phase = "before"
    if mode == "error":
        terminal(issue, phase, "error", sys.argv[4], sys.argv[5])
    elif mode == "refuse":
        terminal(issue, phase, "refused", sys.argv[4], sys.argv[5])
    elif mode == "verdict":
        try:
            verdict_main(issue, phase, sys.argv[4:])
        except Exception as e:
            terminal(issue, phase, "error", "CRASH", f"finalize crashed: {type(e).__name__}: {e}")
            sys.exit(1)
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
