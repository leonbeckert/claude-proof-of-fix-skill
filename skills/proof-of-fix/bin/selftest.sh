#!/bin/bash
# proof-of-fix mechanical selftest: runs the real pipeline against bundled demo
# pages in a throwaway project. Every check is a hard assertion; exit 0 = all green.
# Env: POF_PLAYWRIGHT_ROOT — dir whose node_modules contains playwright
#      (default: bootstrap into <tmp>/pw via npm).
set -uo pipefail
BIN="$(cd "$(dirname "$0")" && pwd)"
DEMO="$BIN/../demo"
PORT=4611
TMP=$(mktemp -d /tmp/pof-selftest.XXXXXX)
WWW="$TMP/www"; mkdir -p "$WWW"
export POF_ROOT="$TMP" POF_NO_OPEN=1
PASS=0; FAILED=0

say() { printf '%s\n' "$*"; }
ok() { PASS=$((PASS + 1)); say "  ok  $1"; }
bad() { FAILED=$((FAILED + 1)); say "  FAIL $1"; }
check() { # check <desc> <cmd...>
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi
}
jget() { python3 -c "import json,sys;d=json.load(open('$1'));print(d$2)"; }
cleanup() { lsof -ti tcp:$PORT | xargs -r kill 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

say "T1 toolchain"
for t in node ffmpeg ffprobe magick python3 lsof curl; do check "tool $t" which "$t"; done

# playwright root
PW="${POF_PLAYWRIGHT_ROOT:-}"
if [[ -z "$PW" ]]; then
  PW="$TMP/pw"; mkdir -p "$PW"
  (cd "$PW" && npm init -y >/dev/null 2>&1 && npm i playwright@1.58.0 --no-audit --no-fund >/dev/null 2>&1)
fi
check "playwright module present" test -d "$PW/node_modules/playwright"

mkdir -p "$TMP/.proof-of-fix"
cat >"$TMP/.proof-of-fix/config.json" <<EOF
{ "app_dir": "www", "port": $PORT, "base_url": "http://localhost:$PORT",
  "health_path": "/", "dev_cmd": "python3 -m http.server $PORT",
  "server_env": {}, "playwright_root": "$PW",
  "auth": {"method": "none"},
  "viewports": {"desktop": {"width": 1280, "height": 720}, "mobile": {"width": 375, "height": 667}},
  "auto_open": false, "restart_on_after": false }
EOF

req() { printf '%s' "$1" >"$TMP/req.json"; echo "$TMP/req.json"; }
run() { (cd "$TMP" && bash "$BIN/phase.sh" capture "$TMP/req.json"); }

say "T2 before-phase with machine assert (video mode)"
cp "$DEMO/interactive-before.html" "$WWW/index.html"
req '{"issue":"itest","phase":"before","route":"/","steps":[{"action":"click","selector":"#save"},{"action":"waitMs","ms":400}],"expect":"Red error text: saving failed","assert":{"type":"text","text":"saving failed"},"expect_after":"Green text: Saved","assert_after":{"type":"text","text":"Saved"}}' >/dev/null
run >/dev/null 2>&1
R="$TMP/.proof-of-fix/itest/result.json"
check "result.json exists" test -f "$R"
check "status success" test "$(jget "$R" "['status']")" = "success"
check "phase before" test "$(jget "$R" "['phase']")" = "before"
check "symptom method machine" test "$(jget "$R" "['symptom_check']['method']")" = "machine"
check "video artifact exists" test -s "$TMP/.proof-of-fix/itest/before/video.webm"
SHA_REC=$(jget "$R" "['artifacts'][0]['sha256']")
SHA_NOW=$(python3 -c "import hashlib;print(hashlib.sha256(open('$TMP/.proof-of-fix/itest/$(jget "$R" "['artifacts'][0]['path']")','rb').read()).hexdigest())")
check "sha binding recomputes" test "$SHA_REC" = "$SHA_NOW"

say "T3 after-phase → mp4 deliverable"
cp "$DEMO/interactive-after.html" "$WWW/index.html" # 'the fix'
req '{"issue":"itest","phase":"after"}' >/dev/null
run >/dev/null 2>&1
check "status success" test "$(jget "$R" "['status']")" = "success"
DELIV="$TMP/.proof-of-fix/itest/$(jget "$R" "['deliverable']")"
check "deliverable mp4 exists" test -s "$DELIV"
check "codec h264" bash -c "ffprobe -v error -show_entries stream=codec_name -of default=nw=1:nk=1 '$DELIV' | grep -q h264"
D=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DELIV")
D1=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$TMP/.proof-of-fix/itest/before/video.webm")
D2=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$TMP/.proof-of-fix/itest/after/video.webm")
check "duration >= longest capture" python3 -c "assert float('$D')+0.05 >= max(float('$D1'),float('$D2'))"
say "T4 proof comment"
PC="$TMP/.proof-of-fix/itest/$(jget "$R" "['proof_comment']")"
check "proof-comment exists" test -s "$PC"
check "proof-comment names issue" grep -q "issue #itest" "$PC"
check "no tmp result file left" bash -c "! ls $TMP/.proof-of-fix/itest/.result.json.tmp 2>/dev/null"

say "T5 still mode (no interactions) → png deliverable"
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"stest","phase":"before","route":"/","steps":[],"expect":"Red banner: layout broken","assert":{"type":"text","text":"Layout broken"},"expect_after":"Green banner: layout correct","assert_after":{"type":"text","text":"Layout correct"}}' >/dev/null
run >/dev/null 2>&1
cp "$DEMO/static-after.html" "$WWW/index.html"
req '{"issue":"stest","phase":"after"}' >/dev/null
run >/dev/null 2>&1
RS="$TMP/.proof-of-fix/stest/result.json"
check "still status success" test "$(jget "$RS" "['status']")" = "success"
check "deliverable is png" test "$(jget "$RS" "['deliverable']")" = "deliverable/before-after.png"
# Stacked, so the deliverable keeps the viewport width — that is the whole point:
# a 2x-wide frame gets scaled to fit and the type it has to prove becomes unreadable.
DW=$(magick identify -format %w "$TMP/.proof-of-fix/stest/deliverable/before-after.png" 2>/dev/null || echo 0)
DH=$(magick identify -format %h "$TMP/.proof-of-fix/stest/deliverable/before-after.png" 2>/dev/null || echo 0)
SH=$(magick identify -format %h "$TMP/.proof-of-fix/stest/after/screenshot.png" 2>/dev/null || echo 0)
check "png keeps viewport width (not widened)" test "$DW" = "1280"
check "png is stacked, not placed side by side" test "$DH" -gt "$((SH * 2))"

say "T5b annotated stills are generated and recorded"
VDIR="$TMP/.proof-of-fix/stest/deliverable/variants"
check "01-full-view always present" test -s "$VDIR/01-full-view.png"
check "02/03 attachment pair present" bash -c "test -s '$VDIR/02-context.png' && test -s '$VDIR/03-line-zoom.png'"
check "blink gif animates" bash -c "python3 -c \"
from PIL import Image; import sys
im = Image.open('$VDIR/04-blink.gif'); sys.exit(0 if im.n_frames == 2 else 1)\""
check "result.json lists the variants" bash -c "python3 -c \"
import json,sys
v = json.load(open('$RS')).get('variants') or []
sys.exit(0 if len(v) >= 4 and all(x.startswith('deliverable/variants/') for x in v) else 1)\""
check "variants are hash-bound in artifacts" bash -c "python3 -c \"
import json,sys
a = {x['path'] for x in json.load(open('$RS'))['artifacts']}
sys.exit(0 if 'deliverable/variants/02-context.png' in a else 1)\""
check "proof-comment names the stills to attach" grep -q "02-context.png" "$TMP/.proof-of-fix/stest/deliverable/proof-comment.md"
check "variants subcommand reruns standalone" bash -c "cd $TMP && bash $BIN/phase.sh variants stest | grep -q 02-context"

say "T6 failing machine assert → SYMPTOM_NOT_VISIBLE"
cp "$DEMO/static-after.html" "$WWW/index.html" # symptom NOT present
req '{"issue":"failtest","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
run >/dev/null 2>&1
RF="$TMP/.proof-of-fix/failtest/result.json"
check "status error" test "$(jget "$RF" "['status']")" = "error"
check "code SYMPTOM_NOT_VISIBLE" test "$(jget "$RF" "['code']")" = "SYMPTOM_NOT_VISIBLE"
check "no deliverable dir" bash -c "! test -e $TMP/.proof-of-fix/failtest/deliverable"

say "T7 verdict guards"
(cd "$TMP" && bash "$BIN/phase.sh" finalize failtest before --verdict confirmed --note "looks broken anyway") >/dev/null 2>&1
check "confirmed cannot override failed assert" test "$(jget "$RF" "['code']")" = "VERDICT_CONFLICT"

say "T8 visual-verdict path (no assert)"
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"vtest","phase":"before","route":"/","steps":[],"expect":"Red banner: layout broken","expect_after":"Green banner"}' >/dev/null
OUT=$(run 2>&1)
check "prints NEEDS_VISUAL_VERDICT" bash -c "grep -q NEEDS_VISUAL_VERDICT <<<'$OUT'"
check "no premature result.json" bash -c "! test -e $TMP/.proof-of-fix/vtest/result.json"
(cd "$TMP" && bash "$BIN/phase.sh" finalize vtest before --verdict confirmed --note "red banner visible top-left") >/dev/null 2>&1
RV="$TMP/.proof-of-fix/vtest/result.json"
check "visual confirm succeeds" test "$(jget "$RV" "['status']")" = "success"
check "method visual" test "$(jget "$RV" "['symptom_check']['method']")" = "visual"

say "T9 after without before → NO_BEFORE"
req '{"issue":"nobefore","phase":"after"}' >/dev/null
run >/dev/null 2>&1
check "NO_BEFORE" test "$(jget "$TMP/.proof-of-fix/nobefore/result.json" "['code']")" = "NO_BEFORE"

say "T10 spec change guard"
req '{"issue":"stest","phase":"after","route":"/other"}' >/dev/null
run >/dev/null 2>&1
check "SPEC_MISMATCH without allow" test "$(jget "$RS" "['code']")" = "SPEC_MISMATCH"
check "a rejected request does not rotate the existing proof away" \
  test -s "$TMP/.proof-of-fix/stest/deliverable/before-after.png"
cp "$DEMO/static-after.html" "$WWW/other.html"
req '{"issue":"stest","phase":"after","route":"/other.html","allow_spec_change":true}' >/dev/null
run >/dev/null 2>&1
check "allowed change succeeds" test "$(jget "$RS" "['status']")" = "success"
check "spec_changed flagged" test "$(jget "$RS" "['spec_changed']")" = "True"
check "the overwritten before-spec is snapshotted, not lost" \
  bash -c "ls $TMP/.proof-of-fix/stest/history/*/capture-spec.json >/dev/null 2>&1"
req '{"issue":"stest","phase":"after","allow_spec_change":true,"steps":[{"action":"teleport","selector":"#x"}]}' >/dev/null
run >/dev/null 2>&1
check "re-sent steps are validated, not trusted" test "$(jget "$RS" "['code']")" = "INVALID_REQUEST"

say "T11 non-localhost → NON_LOCAL_TARGET"
req '{"issue":"remote","phase":"before","route":"http://staging.example.com/x","steps":[],"expect":"x","expect_after":"y"}' >/dev/null
run >/dev/null 2>&1
check "NON_LOCAL_TARGET" test "$(jget "$TMP/.proof-of-fix/remote/result.json" "['code']")" = "NON_LOCAL_TARGET"

say "T12 crash injection → CRASH result via trap"
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"crashtest","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
(cd "$TMP" && POF_TEST_CRASH=1 bash "$BIN/phase.sh" capture "$TMP/req.json") >/dev/null 2>&1
RC="$TMP/.proof-of-fix/crashtest/result.json"
check "crash still writes result.json" test -f "$RC"
check "status error" test "$(jget "$RC" "['status']")" = "error"

say "T13 history rotation keeps 5"
for i in 1 2 3 4 5 6 7; do
  req '{"issue":"histtest","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
  run >/dev/null 2>&1
done
N=$(ls "$TMP/.proof-of-fix/histtest/history" | wc -l | tr -d ' ')
check "history has 5 entries (had 6 rotations)" test "$N" = "5"

say "T14 role guard (validation only)"
python3 - "$TMP/.proof-of-fix/config.json" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); c2=dict(c)
c2["auth"]={"method":"form","login_path":"/","user_selector":"#u","pass_selector":"#p",
            "submit_selector":"#s","post_login_selector":"#d","default_role":None,"roles":{}}
json.dump(c2,open(sys.argv[1].replace("config.json","config-roles.json"),"w"))
EOF
cp "$TMP/.proof-of-fix/config.json" "$TMP/.proof-of-fix/config-none.json"
cp "$TMP/.proof-of-fix/config-roles.json" "$TMP/.proof-of-fix/config.json"
req '{"issue":"roletest","phase":"before","route":"/","steps":[],"expect":"x banner","role":"real_admin","expect_after":"y"}' >/dev/null
run >/dev/null 2>&1
check "UNKNOWN_ROLE" test "$(jget "$TMP/.proof-of-fix/roletest/result.json" "['code']")" = "UNKNOWN_ROLE"
cp "$TMP/.proof-of-fix/config-none.json" "$TMP/.proof-of-fix/config.json"

say "T15 refuse path"
(cd "$TMP" && bash "$BIN/phase.sh" refuse gl99 before FORBIDDEN_ACTION "caller asked to attach to the issue tracker") >/dev/null 2>&1
check "refused status" test "$(jget "$TMP/.proof-of-fix/gl99/result.json" "['status']")" = "refused"
check "FORBIDDEN_ACTION code" test "$(jget "$TMP/.proof-of-fix/gl99/result.json" "['code']")" = "FORBIDDEN_ACTION"

# ---- Regression tests (each pins a validation-round finding) ----

say "REG1 argv → python -c injection is dead (machinery BLOCKER-1)"
rm -f /tmp/pof-pwned-$$
(cd "$TMP" && bash "$BIN/phase.sh" capture "'+str(__import__('os').system('touch /tmp/pof-pwned-$$'))+'") >/dev/null 2>&1 || true
check "no code execution via request-path argv" bash -c "! test -e /tmp/pof-pwned-$$"
rm -f /tmp/pof-pwned-$$

say "REG2 path traversal via issue arg is contained (machinery BLOCKER-2)"
(cd "$TMP" && bash "$BIN/phase.sh" refuse '../../POF_ESCAPE-'"$$" before FORBIDDEN_ACTION x) >/dev/null 2>&1 || true
check "no result.json written outside .proof-of-fix (finalize)" bash -c "! ls $TMP/../POF_ESCAPE-$$ 2>/dev/null && ! ls $TMP/POF_ESCAPE-$$ 2>/dev/null"
check "traversal issue routed to _invalid bucket" test -f "$TMP/.proof-of-fix/_invalid/result.json"

say "REG3 redirect capture is refused (machinery MAJOR-3)"
python3 - <<'PY' &
import http.server, socketserver
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302); self.send_header("Location", "https://example.com/"); self.end_headers()
    def log_message(self, *a): pass
socketserver.TCPServer(("127.0.0.1", 4699), H).serve_forever()
PY
RPID=$!; sleep 1
mkdir -p "$TMP/.proof-of-fix/redir/before"
cat > "$TMP/redir-resolved.json" <<EOF
{ "issue":"redir","phase":"before","url":"http://127.0.0.1:4699/","base_url":"http://127.0.0.1:4699",
  "steps":[],"viewport":{"name":"desktop","width":800,"height":600},"mode":"still",
  "expect":"x","expect_after":"y","assert":null,"role":null,"auth":{"method":"none"},
  "playwright_root":"$PW","phase_dir":"$TMP/.proof-of-fix/redir/before","spec_changed":false,"spec_diff":null }
EOF
REDIR_ERR=$(node "$BIN/record.mjs" "$TMP/redir-resolved.json" 2>&1); REDIR_RC=$?
kill $RPID 2>/dev/null
check "record.mjs rejects redirect to remote" test "$REDIR_RC" != "0"
check "error is NON_LOCAL_TARGET" bash -c "grep -q NON_LOCAL_TARGET <<<'$REDIR_ERR'"
check "no remote screenshot kept" bash -c "! test -s $TMP/.proof-of-fix/redir/before/screenshot.png"

say "REG4 role requested against auth:none → UNKNOWN_ROLE, not silent drop"
req '{"issue":"roledrop","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"role":"admin_real","expect_after":"x"}' >/dev/null
cp "$DEMO/static-before.html" "$WWW/index.html"; run >/dev/null 2>&1
check "UNKNOWN_ROLE on auth:none" test "$(jget "$TMP/.proof-of-fix/roledrop/result.json" "['code']")" = "UNKNOWN_ROLE"

say "REG5 error results carry the captured screenshot pointer"
cp "$DEMO/static-after.html" "$WWW/index.html" # symptom absent
req '{"issue":"errart","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
run >/dev/null 2>&1
check "SYMPTOM_NOT_VISIBLE" test "$(jget "$TMP/.proof-of-fix/errart/result.json" "['code']")" = "SYMPTOM_NOT_VISIBLE"
check "error artifacts include screenshot" python3 -c "import json;a=json.load(open('$TMP/.proof-of-fix/errart/result.json'))['artifacts'];exit(0 if any('screenshot' in x['path'] for x in a) else 1)"

say "REG6 second after-run archives the previous deliverable (no silent destroy)"
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"reruntest","phase":"before","route":"/","steps":[],"expect":"Red banner: layout broken","assert":{"type":"text","text":"Layout broken"},"expect_after":"Green banner","assert_after":{"type":"text","text":"Layout correct"}}' >/dev/null
run >/dev/null 2>&1
cp "$DEMO/static-after.html" "$WWW/index.html"
req '{"issue":"reruntest","phase":"after"}' >/dev/null
run >/dev/null 2>&1
cp "$DEMO/static-after.html" "$WWW/other.html"
req '{"issue":"reruntest","phase":"after","route":"/other.html","allow_spec_change":true}' >/dev/null
run >/dev/null 2>&1
check "history dir created on after re-run" test -d "$TMP/.proof-of-fix/reruntest/history"
check "archived run kept its deliverable" bash -c "ls $TMP/.proof-of-fix/reruntest/history/*/deliverable/before-after.png >/dev/null 2>&1"
check "baseline before/ stayed in place" test -f "$TMP/.proof-of-fix/reruntest/before/meta.json"

say "REG7 server_env value is not eval'd (machinery MAJOR-4)"
rm -f /tmp/pof-evalinj-$$
python3 - "$TMP/.proof-of-fix/config.json" "/tmp/pof-evalinj-$$" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c["server_env"] = {"PWN": f"$(touch {sys.argv[2]})"}
json.dump(c, open(sys.argv[1], "w"))
PY
lsof -ti tcp:$PORT | xargs -r kill 2>/dev/null; sleep 1
(cd "$TMP" && bash "$BIN/ensure_server.sh" before) >/dev/null 2>&1 || true
check "command substitution in server_env did not execute" bash -c "! test -e /tmp/pof-evalinj-$$"
rm -f /tmp/pof-evalinj-$$
python3 - "$TMP/.proof-of-fix/config.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); c["server_env"] = {}
json.dump(c, open(sys.argv[1], "w"))
PY

say "REG8 still mode records no video (clean issue dir)"
lsof -ti tcp:$PORT | xargs -r kill 2>/dev/null; sleep 1
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"novideo","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
run >/dev/null 2>&1
check "still-mode success" test "$(jget "$TMP/.proof-of-fix/novideo/result.json" "['status']")" = "success"
check "no video.webm in still mode" bash -c "! test -e $TMP/.proof-of-fix/novideo/before/video.webm"
check "meta video field null" test "$(jget "$TMP/.proof-of-fix/novideo/before/meta.json" "['video']")" = "None"

say "REG9 variants subcommand rejects a traversal issue (same guard as capture)"
mkdir -p "$TMP/escape-target/before" "$TMP/escape-target/after"
cp "$TMP/.proof-of-fix/itest/before/screenshot.png" "$TMP/escape-target/before/screenshot.png"
cp "$TMP/.proof-of-fix/itest/after/screenshot.png" "$TMP/escape-target/after/screenshot.png"
(cd "$TMP" && bash "$BIN/phase.sh" variants "../escape-target") >/dev/null 2>&1
check "phase.sh refuses the traversal issue" test $? -ne 0
python3 "$BIN/variants.py" "$TMP/escape-target" >/dev/null 2>&1
check "variants.py refuses a dir outside the capture root" test $? -ne 0
check "nothing written outside .proof-of-fix" bash -c "! test -e $TMP/escape-target/deliverable"

say "REG10 a failed step still leaves something to debug from"
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"stepfail","phase":"before","route":"/","steps":[{"action":"click","selector":"#does-not-exist"}],"expect":"Red banner","expect_after":"x"}' >/dev/null
run >/dev/null 2>&1
RSF="$TMP/.proof-of-fix/stepfail/result.json"
check "STEP_FAILED" test "$(jget "$RSF" "['code']")" = "STEP_FAILED"
check "failure screenshot captured" test -s "$TMP/.proof-of-fix/stepfail/before/screenshot.png"
check "error result points at the screenshot" grep -q "before/screenshot.png" "$RSF"
check "no orphan webm left in the phase dir" bash -c "! ls $TMP/.proof-of-fix/stepfail/before/*.webm >/dev/null 2>&1"

say "REG11 the mark is per region, and displacement does not drown the change"
cat > "$TMP/measure_test.py" <<'PY'
import os, sys
sys.path.insert(0, os.environ["POF_BIN"])
from PIL import Image, ImageDraw
import variants as V


def pair(paint_b, paint_a, size=(800, 600)):
    b = Image.new("RGB", size, "white"); paint_b(ImageDraw.Draw(b))
    a = Image.new("RGB", size, "white"); paint_a(ImageDraw.Draw(a))
    return b, a


def marks(b, a):
    """Mirror of build()'s measurement path — cancellation included."""
    w, h = b.size
    keep, dropped = V.select_clusters(
        V.diff_clusters(V.cancel_displacement(V.diff_mask(b, a), b, a)))
    grown = [V.pad_box(V.whole_lines(V.pad_box(x, w, h), a, w, h), w, h, 4, 4, 3, 3)
             for _, x in keep]
    return V.merge_boxes(grown), dropped


case = sys.argv[1]
if case == "distant":
    # A real change top-left and an unrelated blip bottom-right — a ticking
    # timestamp, a spinner. The union of both brackets everything in between.
    b, a = pair(lambda d: (d.rectangle([100, 100, 300, 140], fill="black"),
                           d.rectangle([700, 500, 712, 510], fill="black")),
                lambda d: d.rectangle([100, 100, 300, 140], fill="gray"))
    boxes, dropped = marks(b, a)
    assert len(boxes) == 1, boxes
    assert len(dropped) == 1, dropped
    assert boxes[0][3] < 200, boxes  # must not stretch down to the blip at y=500
elif case == "region":
    # Three reworked rows of one card are one change, not three findings.
    rows = [(100, 100, 300, 140), (100, 170, 300, 210), (100, 240, 300, 280)]
    b, a = pair(lambda d: [d.rectangle(r, fill="black") for r in rows],
                lambda d: [d.rectangle(r, fill="gray") for r in rows])
    boxes, _ = marks(b, a)
    assert len(boxes) == 1, boxes
elif case == "gap1":
    quiet = [False] * 100
    quiet[50] = True
    for i in range(60, 80):
        quiet[i] = True
    assert V.edge_out(40, quiet, +1, 60, 1) == 50
elif case == "insert":
    # An inserted row shifts everything below it. Positionally every shifted row
    # differs, and those ghosts outweigh the insertion by orders of magnitude —
    # unfixed, the selection keeps the ghosts and drops the actual change.
    def rows(items, step=40):
        def paint(d):
            for i, t in enumerate(items):
                d.rectangle([40, 40 + i * step, 140, 40 + i * step + 22], fill=(40, 40, 40))
                d.text((44, 44 + i * step), t, fill="white")
        return paint
    b, a = pair(rows(["A", "B", "C"]), rows(["NEW", "A", "B", "C"]))
    raw = V.diff_mask(b, a)
    cancelled = V.cancel_displacement(raw, b, a)
    assert raw.getbbox()[3] > 150, raw.getbbox()          # ghosts reach down the page
    assert cancelled.getbbox()[3] < 100, cancelled.getbbox()  # only the inserted row left
    boxes, _ = marks(b, a)
    assert len(boxes) == 1, boxes
    assert boxes[0][1] < 70, boxes
elif case == "edit_survives":
    # The cancellation must not eat an edit that happens in place.
    def rows(items):
        def paint(d):
            for i, t in enumerate(items):
                d.rectangle([40, 40 + i * 40, 140, 40 + i * 40 + 22], fill=(40, 40, 40))
                d.text((44, 44 + i * 40), t, fill="white")
        return paint
    b, a = pair(rows(["A", "B", "C"]), rows(["A", "X", "C"]))
    raw = V.diff_mask(b, a)
    assert V.cancel_displacement(raw, b, a).getbbox() == raw.getbbox()
elif case == "moved_only":
    # Content that only moved is still a visible change. Cancelling everything
    # must fall back to the raw mask, not produce an empty variant set.
    def rows(y0):
        def paint(d):
            for i, t in enumerate(["A", "B", "C"]):
                d.rectangle([40, y0 + i * 40, 140, y0 + i * 40 + 22], fill=(40, 40, 40))
                d.text((44, y0 + 4 + i * 40), t, fill="white")
        return paint
    b, a = pair(rows(40), rows(100))
    raw = V.diff_mask(b, a)
    assert V.cancel_displacement(raw, b, a).getbbox() is None  # nothing but displacement
    import os as _os, shutil as _sh
    d = _os.path.join(_os.environ["POF_ROOT"], ".proof-of-fix", "movedcase")
    _sh.rmtree(d, ignore_errors=True)
    for side, img in (("before", b), ("after", a)):
        _os.makedirs(_os.path.join(d, side))
        img.save(_os.path.join(d, side, "screenshot.png"))
    assert V.build(d) == 0, "pure move must still produce variants"
    assert _os.path.isfile(_os.path.join(d, "deliverable", "variants", "01-full-view.png"))
elif case == "wide_no_zoom":
    # A change spanning most of the width cannot be magnified: the readout strip is
    # refitted to the context width, cancelling the enlargement. 05 must then not be
    # written at all — a 1.2x "zoom" is the context view repeated at twice the size.
    # Sized to land between the two gates: wide enough that the readout cannot
    # magnify, small enough in area that 02 is still produced (six full-width rows
    # would trip the 45% cut instead and prove nothing about this branch).
    def bars(wide):
        def paint(d):
            for i in range(4):
                d.rectangle([40, 40 + i * 50, 740 if wide else 120, 40 + i * 50 + 30],
                            fill=(40, 40, 40))
        return paint
    b, a = pair(bars(False), bars(True))
    import os as _os, shutil as _sh
    dd = _os.path.join(_os.environ["POF_ROOT"], ".proof-of-fix", "widecase")
    _sh.rmtree(dd, ignore_errors=True)
    for side, img in (("before", b), ("after", a)):
        _os.makedirs(_os.path.join(dd, side))
        img.save(_os.path.join(dd, side, "screenshot.png"))
    vd = _os.path.join(dd, "deliverable", "variants")
    _os.makedirs(vd)
    # A leftover from an earlier run must not survive a rebuild that no longer
    # produces it, or the set carries one still nothing measured.
    open(_os.path.join(vd, "05-context-with-zoom.png"), "wb").write(b"stale")
    assert V.build(dd) == 0
    made = sorted(_os.listdir(vd))
    assert "02-context.png" in made, made
    assert "05-context-with-zoom.png" not in made, made
elif case == "narrow_keeps_zoom":
    # The counterpart: a small change still gets its readout, so the skip above is
    # a property of wide changes and not of the check itself.
    def label(txt):
        def paint(d):
            d.rectangle([40, 40, 700, 300], fill=(245, 245, 245))
            d.text((60, 160), txt, fill="black")
        return paint
    b, a = pair(label("Not available"), label("Free at 12:00"))
    import os as _os, shutil as _sh
    dd = _os.path.join(_os.environ["POF_ROOT"], ".proof-of-fix", "narrowcase")
    _sh.rmtree(dd, ignore_errors=True)
    for side, img in (("before", b), ("after", a)):
        _os.makedirs(_os.path.join(dd, side))
        img.save(_os.path.join(dd, side, "screenshot.png"))
    assert V.build(dd) == 0
    made = sorted(_os.listdir(_os.path.join(dd, "deliverable", "variants")))
    assert "05-context-with-zoom.png" in made, made
elif case == "gap10":
    # A one-pixel quiet column is a glyph gap, not a component boundary: with a
    # width requirement the expansion walks past it to the real gutter.
    quiet = [False] * 100
    quiet[50] = True
    for i in range(60, 80):
        quiet[i] = True
    assert V.edge_out(40, quiet, +1, 60, 10) == 60
PY
export POF_BIN="$BIN"
for c in distant region gap1 gap10 insert edit_survives moved_only wide_no_zoom narrow_keeps_zoom; do
  check "measurement: $c" python3 "$TMP/measure_test.py" "$c"
done

say "REG12 a piped capture terminates and leaves nothing holding the caller's stdout"
lsof -ti tcp:$PORT | xargs -r kill 2>/dev/null; sleep 1
cp "$DEMO/static-before.html" "$WWW/index.html"
req '{"issue":"pipetest","phase":"before","route":"/","steps":[],"expect":"Red banner","assert":{"type":"text","text":"Layout broken"},"expect_after":"x"}' >/dev/null
# Every other check redirects to a file, which is exactly why the hang hid here:
# only a pipe reader waits for the write end to close.
( cd "$TMP" && bash "$BIN/phase.sh" capture "$TMP/req.json" 2>&1 | cat >"$TMP/pipe.out" ) &
PIPEPID=$!
WAITED=0
while kill -0 "$PIPEPID" 2>/dev/null && [[ $WAITED -lt 90 ]]; do sleep 1; WAITED=$((WAITED + 1)); done
check "piped capture returned instead of blocking on an inherited pipe" \
  bash -c "! kill -0 $PIPEPID 2>/dev/null"
kill -9 "$PIPEPID" 2>/dev/null
check "piped run reported success" grep -q "SUCCESS before" "$TMP/pipe.out"
# The bracket keeps pgrep from matching this very command line.
check "no server supervisor survived the run" bash -c "! pgrep -f 'ensure_server[.]sh' >/dev/null"

say ""
say "selftest: $PASS passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
