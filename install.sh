#!/bin/bash
# Install the proof-of-fix skills into a target project.
#
# Copies both skills into <project>/.claude/skills/, adds the permission rules a
# headless run needs, and gitignores the capture directory. Everything it writes is
# printed. It never touches anything else in the project.
#
# Usage: ./install.sh /path/to/project [--force]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
FORCE="${2:-}"

die() { echo "error: $*" >&2; exit 1; }

[[ -n "$TARGET" ]] || die "usage: ./install.sh /path/to/project [--force]"
[[ -d "$TARGET" ]] || die "not a directory: $TARGET"
TARGET="$(cd "$TARGET" && pwd)"
[[ "$TARGET" != "$SRC" ]] || die "target is this repo; pick the project you want to install into"

echo "installing proof-of-fix"
echo "  from: $SRC"
echo "  into: $TARGET"
echo

# --- dependencies -------------------------------------------------------------
# Checked here rather than at capture time: a missing binary should stop the install,
# not surface hours later as a half-written result.json.
missing=()
for bin in ffmpeg ffprobe magick python3 node; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
python3 -c 'import PIL' >/dev/null 2>&1 || missing+=("python3 Pillow (pip3 install Pillow)")
FONT="/System/Library/Fonts/Helvetica.ttc"
[[ -f "$FONT" ]] || missing+=("$FONT (macOS only — see README Limits)")

if ((${#missing[@]})); then
  echo "missing dependencies:"
  printf '  - %s\n' "${missing[@]}"
  echo
  [[ "$FORCE" == "--force" ]] || die "install anyway with --force, but captures will fail until these exist"
  echo "continuing because --force was given"
  echo
fi

# --- skills -------------------------------------------------------------------
SKILLS="$TARGET/.claude/skills"
mkdir -p "$SKILLS"
for skill in proof-of-fix proof-of-fix-setup; do
  if [[ -d "$SKILLS/$skill" && "$FORCE" != "--force" ]]; then
    die "$SKILLS/$skill already exists; rerun with --force to overwrite"
  fi
  rm -rf "${SKILLS:?}/$skill"
  cp -R "$SRC/skills/$skill" "$SKILLS/$skill"
  chmod +x "$SKILLS/$skill"/bin/*.sh 2>/dev/null || true
  echo "  wrote  .claude/skills/$skill"
done

# --- contract doc -------------------------------------------------------------
mkdir -p "$TARGET/docs"
if [[ ! -f "$TARGET/docs/proof-of-fix-contract.md" || "$FORCE" == "--force" ]]; then
  cp "$SRC/docs/contract.md" "$TARGET/docs/proof-of-fix-contract.md"
  echo "  wrote  docs/proof-of-fix-contract.md"
fi

# --- gitignore ----------------------------------------------------------------
# Captures are screenshots of a logged-in app. Even against seed data they do not
# belong in git history.
GI="$TARGET/.gitignore"
if ! { [[ -f "$GI" ]] && grep -qx '\.proof-of-fix/' "$GI"; }; then
  printf '\n# proof-of-fix captures\n.proof-of-fix/\n' >> "$GI"
  echo "  wrote  .gitignore  (+ .proof-of-fix/)"
else
  echo "  ok     .gitignore already ignores .proof-of-fix/"
fi

# --- permissions --------------------------------------------------------------
# Without these, a headless run is denied silently and the caller sees a timeout
# rather than a permission error.
SETTINGS="$TARGET/.claude/settings.local.json"
python3 - "$SETTINGS" <<'PY'
import json, os, sys
path = sys.argv[1]
rules = [
    "Bash(bash .claude/skills/proof-of-fix/bin/phase.sh:*)",
    "Bash(bash .claude/skills/proof-of-fix/bin/selftest.sh:*)",
    "Read(.proof-of-fix/**)",
    # Edit(...) covers every file-editing tool including Write. A separate
    # Write(...) rule is not matched by file permission checks at all, and
    # Claude Code warns about it on every invocation.
    #
    # Scoped to the request file on purpose: it is the only thing the agent
    # writes. result.json, capture-spec.json and everything under deliverable/
    # are produced by the pipeline through the Bash rule above, and SKILL.md
    # forbids editing them by hand — so the agent never needs write access to
    # them, and granting it would let a confused run forge its own proof.
    "Edit(.proof-of-fix/**/request-*.json)",
]
data = {}
if os.path.isfile(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit(f"error: {path} is not valid JSON — fix or move it, then rerun")
perms = data.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
added = [r for r in rules if r not in allow]
allow.extend(added)
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
rel = ".claude/settings.local.json"
print(f"  wrote  {rel}  (+{len(added)} allow rule(s))" if added
      else f"  ok     {rel} already has the allow rules")
PY

echo
echo "next:"
echo "  1. bash $TARGET/.claude/skills/proof-of-fix/bin/selftest.sh"
echo "     87 checks against bundled static pages. Red here means stop."
echo "  2. In the project, run /proof-of-fix-setup once, with a human present."
echo "     It writes .proof-of-fix/config.json and proves it with a live smoke capture."
