#!/bin/bash
# Single entry point for all proof-of-fix pipeline work. Guarantees that every
# failure path still ends in a result.json (crash trap), because callers poll
# only that file.
#
# Usage:
#   phase.sh capture  <request.json>
#   phase.sh finalize <issue> <phase> --verdict machine|confirmed|not-visible [--note "..."]
#   phase.sh refuse   <issue> <phase> <CODE> "<detail>"
#   phase.sh variants <issue>          # rebuild the annotated stills only
#
# SECURITY: this script's argv is untrusted (a colluding/injected agent can only
# reach the pipeline through the one granted `Bash(...phase.sh:*)` permission, so
# it controls these args). NEVER string-interpolate an argv value into `python3
# -c "..."`; always pass it as sys.argv to a quoted heredoc. finalize.py and
# validate.py re-validate `issue` against [A-Za-z0-9._-]+ so a traversal value
# cannot escape .proof-of-fix/.
set -uo pipefail
BIN="$(cd "$(dirname "$0")" && pwd)"
export POF_ROOT="${POF_ROOT:-$PWD}"
CMD="${1:-}"; shift || true

fin_error() { python3 "$BIN/finalize.py" error "$ISSUE" "$PHASE" "$1" "$2" || true; }

# read_field <file> <field> <default> <regex> — safe JSON field extraction (argv, not -c)
read_field() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json, re, sys
path, field, default, pat = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    v = json.load(open(path)).get(field, "")
except Exception:
    v = ""
if not isinstance(v, str):
    v = ""
if pat and not re.fullmatch(pat, v):
    v = ""
print(v or default)
PY
}

# meta_field <issue> <phase> <python-path-into-assert-dict> — safe meta.json read
meta_field() {
  python3 - "$POF_ROOT/.proof-of-fix/$1/$2/meta.json" "$3" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))["assert"]
    print(d[sys.argv[2]])
except Exception:
    print("")
PY
}

case "$CMD" in
  capture)
    REQ="${1:?request.json path required}"
    # lenient extraction so even a broken request still lands a result.json
    ISSUE=$(read_field "$REQ" issue _invalid '[A-Za-z0-9._-]+')
    PHASE=$(read_field "$REQ" phase before 'before|after')
    DONE=0; STAGE=start
    trap 'code=$?; if [[ $code -ne 0 && $DONE -eq 0 ]]; then fin_error CRASH "pipeline aborted at stage $STAGE (exit $code)"; fi' EXIT

    STAGE=validate
    OUT=$(python3 "$BIN/validate.py" "$REQ"); RC=$?
    if [[ $RC -eq 3 ]]; then
      CODE=$(printf '%s' "$OUT" | python3 -c "import json,sys;print(json.load(sys.stdin)['code'])")
      DETAIL=$(printf '%s' "$OUT" | python3 -c "import json,sys;print(json.load(sys.stdin)['detail'])")
      fin_error "$CODE" "$DETAIL"; DONE=1; exit 1
    elif [[ $RC -ne 0 ]]; then
      exit $RC # trap writes CRASH
    fi
    RESOLVED="${OUT#OK }"

    STAGE=server
    if ! bash "$BIN/ensure_server.sh" "$PHASE"; then
      fin_error SERVER_UNREACHABLE "dev server never became healthy — see .proof-of-fix/server.log"
      DONE=1; exit 1
    fi

    STAGE=record
    if ! ERR=$(node "$BIN/record.mjs" "$RESOLVED" 2>&1); then
      case "$ERR" in
        AUTH_FAILED*) CODE=AUTH_FAILED ;;
        STEP_FAILED*) CODE=STEP_FAILED ;;
        NON_LOCAL_TARGET*) CODE=NON_LOCAL_TARGET ;;
        *) CODE=CRASH ;;
      esac
      fin_error "$CODE" "${ERR:0:600}"; DONE=1; exit 1
    fi

    STAGE=verdict
    DEFINED=$(meta_field "$ISSUE" "$PHASE" defined)
    PASSED=$(meta_field "$ISSUE" "$PHASE" passed)
    if [[ "$DEFINED" == "True" && "$PASSED" == "True" ]]; then
      python3 "$BIN/finalize.py" verdict "$ISSUE" "$PHASE" --verdict machine; DONE=1
    elif [[ "$DEFINED" == "True" ]]; then
      DETAIL=$(meta_field "$ISSUE" "$PHASE" detail)
      [[ "$PHASE" == "before" ]] && CODE=SYMPTOM_NOT_VISIBLE || CODE=FIX_NOT_VISIBLE
      fin_error "$CODE" "machine assert failed: $DETAIL"; DONE=1; exit 1
    else
      DONE=1
      echo "NEEDS_VISUAL_VERDICT $POF_ROOT/.proof-of-fix/$ISSUE/$PHASE/screenshot.png"
      echo "Read the screenshot, then run: phase.sh finalize $ISSUE $PHASE --verdict confirmed|not-visible --note \"...\""
    fi
    ;;
  finalize)
    ISSUE="${1:?}"; PHASE="${2:?}"; shift 2
    python3 "$BIN/finalize.py" verdict "$ISSUE" "$PHASE" "$@"
    ;;
  refuse)
    ISSUE="${1:?}"; PHASE="${2:?}"; CODE="${3:?}"; DETAIL="${4:?}"
    python3 "$BIN/finalize.py" refuse "$ISSUE" "$PHASE" "$CODE" "$DETAIL"
    ;;
  variants)
    # Rebuild the annotated stills from captures already on disk. Cheap and
    # side-effect free (touches only deliverable/variants/), so it is safe to
    # re-run after tweaking the generator. Does NOT rewrite result.json.
    #
    # `issue` is argv, so it gets the same regex the capture path applies. Without
    # it, `variants ../../elsewhere` reads and writes a directory outside
    # .proof-of-fix/ entirely. REG9 pins this.
    ISSUE="${1:?issue required}"
    if [[ ! "$ISSUE" =~ ^[A-Za-z0-9._-]+$ || "$ISSUE" == "." || "$ISSUE" == ".." ]]; then
      echo "issue must match [A-Za-z0-9._-]+" >&2
      exit 2
    fi
    python3 "$BIN/variants.py" "$POF_ROOT/.proof-of-fix/$ISSUE"
    ;;
  *)
    echo "usage: phase.sh capture <request.json> | finalize <issue> <phase> --verdict ... | refuse <issue> <phase> <CODE> <detail> | variants <issue>" >&2
    exit 2
    ;;
esac
