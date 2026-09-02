#!/bin/bash
# Composite the labeled before/after deliverable from before/ and after/ captures.
# Usage: compose.sh <issue_dir> <video|still>
# Recipe verified 2026-08-14 (research/manifest.md): Homebrew ffmpeg has no
# drawtext, so labels are magick badges; QuickTime can't play webm, so video
# output is h264/yuv420p mp4. Labels sit in a header BAND above the content, not
# overlaid on it, because top-of-page banners are the most common visible symptom
# and must stay unobstructed.
#
# Stacked (before OVER after), never left/right. Two 1920-wide pages side by side
# make a 3840-wide frame that every player and every issue tracker scales down to
# window width, halving the type at the exact moment it has to be legible. Stacking
# costs scrolling, which costs no sharpness. Same reason the stills stack.
#
# Keep the #!/bin/bash line. Under zsh, "$BANDH:color" is read as a parameter
# modifier and the ":c" is swallowed, so ffmpeg receives "64olor=" and fails with a
# message that points nowhere near the cause.
set -euo pipefail
DIR="$1"; MODE="$2"
DEL="$DIR/deliverable"; mkdir -p "$DEL"
FONT="/System/Library/Fonts/Helvetica.ttc"
[[ -f "$FONT" ]] || { echo "label font missing: $FONT" >&2; exit 5; }
BANDH=64

mklabel() { magick -background '#111111' -fill white -font "$FONT" -pointsize 34 -gravity center label:" $1 " "$2"; }
mklabel BEFORE "$DEL/label-before.png"
mklabel AFTER "$DEL/label-after.png"

if [[ "$MODE" == "still" ]]; then
  for side in before after; do
    W=$(magick identify -format %w "$DIR/$side/screenshot.png")
    LBL=$(printf '%s' "$side" | tr '[:lower:]' '[:upper:]')
    magick -size "${W}x${BANDH}" -background '#111111' -fill white -font "$FONT" \
      -pointsize 34 -gravity center label:"$LBL" "$DEL/band-$side.png"
    magick "$DEL/band-$side.png" "$DIR/$side/screenshot.png" -append "$DEL/.$side.png"
  done
  magick "$DEL/.before.png" "$DEL/.after.png" -append "$DEL/before-after.png"
  rm -f "$DEL/.before.png" "$DEL/.after.png" "$DEL/band-before.png" "$DEL/band-after.png"
else
  D1=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DIR/before/video.webm")
  D2=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DIR/after/video.webm")
  MAX=$(python3 -c "print(max(float('$D1'),float('$D2'))+0.5)")
  # tpad clone freezes the shorter side's final frame so both sides stay comparable;
  # pad adds a top band and the label overlays INTO the band (y=15), above content.
  ffmpeg -y -v error \
    -i "$DIR/before/video.webm" -i "$DIR/after/video.webm" \
    -i "$DEL/label-before.png" -i "$DEL/label-after.png" \
    -filter_complex "
      [0:v]tpad=stop_mode=clone:stop_duration=60,trim=duration=$MAX,setpts=PTS-STARTPTS,pad=iw:ih+$BANDH:0:$BANDH:color=#111111[l0];
      [l0][2:v]overlay=(main_w-overlay_w)/2:15[l];
      [1:v]tpad=stop_mode=clone:stop_duration=60,trim=duration=$MAX,setpts=PTS-STARTPTS,pad=iw:ih+$BANDH:0:$BANDH:color=#111111[r0];
      [r0][3:v]overlay=(main_w-overlay_w)/2:15[r];
      [l][r]vstack=inputs=2,pad=ceil(iw/2)*2:ceil(ih/2)*2[v]" \
    -map "[v]" -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart \
    "$DEL/before-after.mp4"
fi
