#!/usr/bin/env python3
"""Validate a proof-of-fix request and resolve it against config + stored spec.

Usage: validate.py <request.json>
  stdout "OK <resolved.json path>" and exit 0, or
  stdout {"code":..., "detail":...} and exit 3 (contract violation).
Side effects: before-phase history rotation, capture-spec.json (re)write.
"""
import json, os, re, shutil, sys, datetime
from urllib.parse import urlparse

INTERACTIONS = {"click", "fill", "press", "hover", "select"}
ACTIONS = {
    "goto": ["path"], "click": ["selector"], "hover": ["selector"],
    "fill": ["selector", "value"], "press": ["key"],
    "select": ["selector", "value"], "waitFor": ["selector"], "waitMs": ["ms"],
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}  # urlparse strips the brackets, WHATWG URL keeps them
SPEC_FIELDS = ["route", "steps", "viewport", "role", "mode", "assert", "assert_after", "expect", "expect_after"]


def fail(code, detail):
    print(json.dumps({"code": code, "detail": detail}))
    sys.exit(3)


def canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def archive_run(issue_dir, items, keep=()):
    """Move `items` into history/<ts>/ (keeps last 5). `keep` items stay in place.

    Returns the history directory so a caller can add copies of files it kept.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    hist = os.path.join(issue_dir, "history", ts)
    os.makedirs(hist, exist_ok=True)
    for item in items:
        if item in keep:
            continue
        src = os.path.join(issue_dir, item)
        if os.path.exists(src):
            shutil.move(src, os.path.join(hist, item))
    runs = sorted(os.listdir(os.path.join(issue_dir, "history")))
    for old in runs[:-5]:
        shutil.rmtree(os.path.join(issue_dir, "history", old))
    return hist


def check_local(url):
    host = urlparse(url).hostname or ""
    if host not in LOCAL_HOSTS:
        fail("NON_LOCAL_TARGET", f"host '{host}' is not localhost — captures run against local dev only")


def check_assert(a, name):
    if a is None:
        return None
    if not isinstance(a, dict) or a.get("type") not in ("text", "visible", "hidden"):
        fail("INVALID_REQUEST", f"{name}.type must be text|visible|hidden")
    if a["type"] == "text" and not a.get("text"):
        fail("INVALID_REQUEST", f"{name}.text required for type=text")
    if a["type"] in ("visible", "hidden") and not a.get("selector"):
        fail("INVALID_REQUEST", f"{name}.selector required for type={a['type']}")
    return a


def check_steps(steps):
    if not isinstance(steps, list):
        fail("INVALID_REQUEST", "steps must be a list (may be empty)")
    if len(steps) > 30:
        fail("INVALID_REQUEST", "max 30 steps")
    for i, s in enumerate(steps):
        a = s.get("action") if isinstance(s, dict) else None
        if a not in ACTIONS:
            fail("INVALID_REQUEST", f"steps[{i}].action must be one of {sorted(ACTIONS)}")
        for pname in ACTIONS[a]:
            if pname not in s:
                fail("INVALID_REQUEST", f"steps[{i}] ({a}) needs '{pname}'")
        if a == "waitMs" and not (isinstance(s["ms"], int) and 0 < s["ms"] <= 10000):
            fail("INVALID_REQUEST", f"steps[{i}].ms must be 1..10000")
    return steps


def check_route(route):
    if not isinstance(route, str) or not route.strip():
        fail("INVALID_REQUEST", "route must be a non-empty string")
    if route.startswith("http"):
        check_local(route)
    elif not route.startswith("/"):
        fail("INVALID_REQUEST", "route must start with '/' or be an absolute localhost URL")
    return route


def check_mode(mode):
    if mode not in ("auto", "video", "still"):
        fail("INVALID_REQUEST", "mode must be auto|video|still")
    return mode


def resolve_viewport(v, cfg):
    table = cfg.get("viewports", {"desktop": {"width": 1920, "height": 1080},
                                  "mobile": {"width": 375, "height": 667}})
    if v is None:
        v = "desktop"
    if isinstance(v, str):
        if v not in table:
            fail("INVALID_REQUEST", f"viewport '{v}' not in config viewports {sorted(table)}")
        return {"name": v, **table[v]}
    if isinstance(v, dict) and isinstance(v.get("width"), int) and isinstance(v.get("height"), int) \
            and 200 <= v["width"] <= 4000 and 200 <= v["height"] <= 4000:
        return {"name": "custom", "width": v["width"], "height": v["height"]}
    fail("INVALID_REQUEST", "viewport must be a config key or {width,height} ints 200..4000")


def main():
    root = os.environ.get("POF_ROOT", os.getcwd())
    cfg_path = os.path.join(root, ".proof-of-fix", "config.json")
    if not os.path.isfile(cfg_path):
        fail("NO_CONFIG", f"{cfg_path} missing — run /proof-of-fix-setup in this project first")
    cfg = json.load(open(cfg_path))
    try:
        req = json.load(open(sys.argv[1]))
    except Exception as e:
        fail("INVALID_REQUEST", f"request is not valid JSON: {e}")

    issue = req.get("issue")
    if not isinstance(issue, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", issue):
        fail("INVALID_REQUEST", "issue must match [A-Za-z0-9._-]+")
    phase = req.get("phase")
    if phase not in ("before", "after"):
        fail("INVALID_REQUEST", "phase must be before|after")

    base = cfg.get("base_url", "")
    check_local(base)
    issue_dir = os.path.join(root, ".proof-of-fix", issue)

    auth = cfg.get("auth", {"method": "none"})

    def resolve_role(role):
        if auth.get("method", "none") == "none":
            # Don't silently drop a requested role — the caller would never learn
            # their capture ran unauthenticated. Only the absence of a role is OK.
            if role:
                fail("UNKNOWN_ROLE",
                     f"role '{role}' requested but this project's auth.method is 'none' — no roles exist here")
            return None
        role = role or auth.get("default_role")
        roles = auth.get("roles", {})
        if not role or role not in roles:
            fail("UNKNOWN_ROLE", f"role '{role}' not in config roles {sorted(roles)} — only seed accounts are allowed")
        return role

    if phase == "before":
        missing = [f for f in ("route", "expect", "expect_after")
                   if not isinstance(req.get(f), str) or not req.get(f).strip()]
        if not isinstance(req.get("steps"), list):
            missing.append("steps (list, may be empty)")
        if missing:
            fail("INVALID_REQUEST", "missing/empty required fields: " + ", ".join(missing))
        steps = check_steps(req["steps"])
        route = check_route(req["route"])
        spec = {
            "route": route, "steps": steps,
            "expect": req["expect"].strip(), "expect_after": req["expect_after"].strip(),
            "assert": check_assert(req.get("assert"), "assert"),
            "assert_after": check_assert(req.get("assert_after"), "assert_after"),
            "viewport": resolve_viewport(req.get("viewport"), cfg),
            "mode": req.get("mode") or "auto",
            "role": resolve_role(req.get("role")),
        }
        check_mode(spec["mode"])
        if spec["mode"] == "auto":
            spec["mode"] = "video" if any(s["action"] in INTERACTIONS for s in steps) else "still"

        # history rotation: a fresh 'before' archives the previous completed run
        if os.path.exists(os.path.join(issue_dir, "result.json")):
            archive_run(issue_dir, ("result.json", "capture-spec.json", "before", "after", "deliverable",
                                    "before-resolved.json", "after-resolved.json",
                                    "request-before.json", "request-after.json"))
        os.makedirs(issue_dir, exist_ok=True)
        with open(os.path.join(issue_dir, "capture-spec.json"), "w") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
        spec_changed, spec_diff = False, None
    else:  # after — replay the stored spec
        spec_path = os.path.join(issue_dir, "capture-spec.json")
        if not (os.path.isfile(spec_path) and os.path.isfile(os.path.join(issue_dir, "before", "meta.json"))):
            fail("NO_BEFORE", f"no completed before-run stored for issue '{issue}'")
        spec = json.load(open(spec_path))
        sent = {k: req[k] for k in SPEC_FIELDS if k in req}
        if "viewport" in sent:
            sent["viewport"] = resolve_viewport(sent["viewport"], cfg)
        if "role" in sent:
            sent["role"] = resolve_role(sent["role"])
        for k in ("assert", "assert_after"):
            if k in sent:
                sent[k] = check_assert(sent[k], k)
        # allow_spec_change lets the caller resend these, so they arrive
        # unvalidated unless they go through the same checks as a before-request.
        if "steps" in sent:
            check_steps(sent["steps"])
        if "route" in sent:
            check_route(sent["route"])
        if "mode" in sent:
            check_mode(sent["mode"])
        diffs = {k: {"old": spec.get(k), "new": v} for k, v in sent.items()
                 if canon(spec.get(k)) != canon(v)}
        if diffs and not req.get("allow_spec_change"):
            fail("SPEC_MISMATCH",
                 "request changes the pinned spec fields "
                 f"{sorted(diffs)} without allow_spec_change=true — before/after must be comparable")
        spec_changed, spec_diff = bool(diffs), (diffs or None)

        # Everything above only reads. Rotation starts here, after the request is
        # known good: a rejected request must not move the caller's current proof
        # out from under the result.json that still points at it.
        #
        # A previous after-run's deliverable would otherwise be overwritten in
        # place. allow_spec_change exists precisely to run a second, different
        # after — archive the first proof instead of destroying it. The shared
        # before/ + capture-spec.json stay as the baseline.
        hist = None
        if os.path.isfile(os.path.join(issue_dir, "after", "meta.json")):
            hist = archive_run(issue_dir, ("result.json", "after", "deliverable",
                                           "after-resolved.json", "request-after.json"),
                               keep=("before", "capture-spec.json"))
        if diffs:
            # capture-spec.json records what the before phase actually executed.
            # Overwriting it in place would leave the archived proof describing
            # steps that were never run, so snapshot it beside its own run first.
            shutil.copy2(spec_path, os.path.join(hist or archive_run(issue_dir, ()),
                                                 "capture-spec.json"))
            spec.update({k: v["new"] for k, v in diffs.items()})
            if spec.get("mode") == "auto":
                spec["mode"] = "video" if any(s["action"] in INTERACTIONS for s in spec["steps"]) else "still"
            with open(spec_path, "w") as f:
                json.dump(spec, f, indent=2, ensure_ascii=False)

    url = spec["route"] if spec["route"].startswith("http") else base.rstrip("/") + spec["route"]
    check_local(url)
    phase_dir = os.path.join(issue_dir, phase)
    role = spec.get("role")
    resolved = {
        "issue": issue, "phase": phase, "url": url, "base_url": base,
        "steps": spec["steps"], "viewport": spec["viewport"], "mode": spec["mode"],
        "expect": spec["expect"], "expect_after": spec["expect_after"],
        "assert": spec["assert"] if phase == "before" else spec.get("assert_after"),
        "role": role, "auth": {**auth, "role_entry": (auth.get("roles", {}).get(role) if role else None)},
        "playwright_root": os.path.abspath(os.path.join(root, cfg.get("playwright_root", "."))),
        "phase_dir": phase_dir, "spec_changed": spec_changed, "spec_diff": spec_diff,
    }
    os.makedirs(phase_dir, exist_ok=True)
    out = os.path.join(issue_dir, f"{phase}-resolved.json")
    with open(out, "w") as f:
        json.dump(resolved, f, indent=2, ensure_ascii=False)
    print(f"OK {out}")


if __name__ == "__main__":
    main()
