#!/usr/bin/env python3
"""Build the annotated still variants that actually get attached to the issue.

Why this exists: a before/after video is a poor proof medium. The reviewer has to
hold two moving images in their head and hunt for the delta. In practice the
delta is often a single line of text, and the honest way to show it is a still
with that line marked.

The marking is not authored, it is measured. Everything here derives from the
pixel difference between before/screenshot.png and after/screenshot.png, so the
box cannot point at the wrong thing, and a variant set that highlights a region
nobody changed is impossible by construction.

Usage: variants.py <issue_dir>
Writes <issue_dir>/deliverable/variants/ and prints one relative path per line.
Exit 0 = files written, 4 = skipped with a reason on stderr (never fatal for the
caller: a presentation nicety must not turn a valid proof into a failed run).
"""
import difflib
import os
import sys

MIN_PIL = "Pillow (pip install Pillow)"
try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - environment guard
    print(f"variants skipped: {MIN_PIL} not installed", file=sys.stderr)
    sys.exit(4)

# --- house style -------------------------------------------------------------
RED, GREEN, MUTED, PAGE_BG = (204, 41, 41), (21, 128, 61), (107, 114, 128), (243, 244, 246)

# --- measurement tuning ------------------------------------------------------
CELL = 8          # cluster grid; finer than this just tracks antialiasing
MERGE_CELLS = 2   # bridge gaps up to 2 cells so one word stays one cluster
KEEP_RATIO = 0.18  # a cluster lighter than this share of the main one is noise
ROW_GAP = 4       # px a quiet band must span to count as a line break
COL_GAP = 10      # px a quiet band must span to count as a component boundary
MERGE_PX = 64     # boxes nearer than this on both axes are one region, not two
LBL_BEFORE, LBL_AFTER = "BEFORE", "AFTER"  # same wording compose.sh burns into the video
BAND_H, BAND_FS = 62, 26

FONT_CANDIDATES = (
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("/Library/Fonts/Arial Bold.ttf", "/Library/Fonts/Arial.ttf"),
)


def _fonts():
    for bold, reg in FONT_CANDIDATES:
        if os.path.isfile(bold) and os.path.isfile(reg):
            return bold, reg
    return None, None


BOLD_F, REG_F = _fonts()


def font(size, bold=True):
    path = BOLD_F if bold else REG_F
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


# --- measurement -------------------------------------------------------------
def diff_mask(before, after, threshold=24):
    """Binary mask of everything that changed, ignoring antialiasing jitter.

    A raw difference catches sub-pixel text rendering noise and would happily
    cover the whole page. Thresholding first keeps the mask on the real change.
    """
    d = ImageChops.difference(before, after).convert("L")
    return d.point(lambda p: 255 if p > threshold else 0)


def diff_clusters(mask, cell=CELL, merge=MERGE_CELLS):
    """Connected components of the diff mask, heaviest first.

    getbbox() over the whole mask returns the union of everything that changed.
    Two unrelated changes — the edited label, and a relative timestamp that ticked
    over while the capture ran — then yield one box bracketing every untouched
    element between them, and a reviewer reads that as "these changed too".
    Components keep them apart.

    Grouping happens on a coarse grid so a word is one cluster and not one per
    glyph; the returned box is then re-measured on the full-resolution mask, so
    the grid groups but never inflates.
    """
    w, h = mask.size
    gw, gh = max(1, -(-w // cell)), max(1, -(-h // cell))
    small = mask.resize((gw, gh), Image.BOX)
    px = small.load()
    sx, sy = w / float(gw), h / float(gh)
    live = {(x, y) for y in range(gh) for x in range(gw) if px[x, y]}
    seen, out = set(), []
    for start in live:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx in range(-merge, merge + 1):
                for dy in range(-merge, merge + 1):
                    nb = (cx + dx, cy + dy)
                    if nb in live and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        xs, ys = [c[0] for c in comp], [c[1] for c in comp]
        coarse = (int(min(xs) * sx), int(min(ys) * sy),
                  min(w, int((max(xs) + 1) * sx) + 1), min(h, int((max(ys) + 1) * sy) + 1))
        exact = mask.crop(coarse).getbbox()
        if not exact:
            continue
        out.append((sum(px[x, y] for x, y in comp),
                    (coarse[0] + exact[0], coarse[1] + exact[1],
                     coarse[0] + exact[2], coarse[1] + exact[3])))
    out.sort(key=lambda t: -t[0])
    return out


def merge_boxes(boxes, gap=MERGE_PX):
    """Fuse expanded boxes that sit within the same region of the page.

    Clustering answers "which pixels changed together". This answers the different
    question "how many marks should a reviewer see". Seven rectangles down one
    redesigned panel is not seven findings, it is one; two marks four hundred
    pixels apart really are two. Merging is by edge distance, so boxes that touch
    or nearly touch collapse and distant ones stay separate.
    """
    out = list(boxes)
    fused = True
    while fused:
        fused = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if (max(0, max(a[0], b[0]) - min(a[2], b[2])) <= gap
                        and max(0, max(a[1], b[1]) - min(a[3], b[3])) <= gap):
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3]))
                    del out[j]
                    fused = True
                    break
            if fused:
                break
    return sorted(out, key=lambda b: (b[1], b[0]))


def select_clusters(clusters, keep_ratio=KEEP_RATIO):
    """Split clusters into the ones worth marking and the ones that are noise.

    Kept by share of the heaviest cluster, not by absolute size: what counts as
    negligible depends on how big the real change is. The list is already sorted,
    so the kept set is a prefix.
    """
    if not clusters:
        return [], []
    cut = keep_ratio * clusters[0][0]
    keep = [c for c in clusters if c[0] >= cut]
    return keep, clusters[len(keep):]


def content_bottom(img, margin=24):
    """Last row carrying content, so a mostly-empty page does not dominate the crop.

    Dashboards are usually a short list on a tall viewport; keeping 500px of empty
    background shrinks the interesting part of every stacked variant.
    """
    w, h = img.size
    bg = img.getpixel((w - 4, h - 4))
    px = img.load()
    step = max(1, w // 240)
    for y in range(h - 1, -1, -1):
        for x in range(0, w, step):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 24:
                return min(h, y + margin)
    return h


def content_left(img, y0, y1, margin=8):
    """First column carrying content within a horizontal band.

    Bounds the focus crop. Cropping right of this cuts into the row itself, and
    the row label is the context the cropped variant exists to supply.
    """
    w, h = img.size
    bg = img.getpixel((w - 4, h - 4))
    px = img.load()
    y0, y1 = max(0, y0), min(h, y1)
    step = max(1, (y1 - y0) // 200)
    for x in range(w):
        for y in range(y0, y1, step):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 24:
                return max(0, x - margin)
    return 0


def _row_signatures(img, quant=8, sample=2):
    """Per-row fingerprint, tolerant to antialiasing jitter but not to real change.

    Rows are compared as quantized grayscale bytes (steps of `quant`, i.e. sub-
    threshold rendering noise collapses to the same signature) at half horizontal
    resolution. A row whose signature is uniform is `blank` — background, gutters,
    separators — and treated as accounted-for on both sides of the alignment.
    """
    w, h = img.size
    g = img.convert("L").resize((max(1, w // sample), h), Image.BOX)
    px = g.load()
    gw = g.size[0]
    sigs, blank = [], []
    for y in range(h):
        row = bytes(px[x, y] // quant for x in range(gw))
        sigs.append(row)
        blank.append(min(row) == max(row))
    return sigs, blank


def cancel_displacement(mask, before, after):
    """Remove displacement ghosts from the diff mask before anything is weighted.

    Inserting one line shifts every row below it. A positional diff then marks all
    of that shifted-but-identical content as changed, and those ghost regions
    outweigh the actual change by orders of magnitude — the cluster selection
    inverts and marks exactly the elements nobody touched (observed on a back-link
    insertion: the link weighed 1.5% of the heaviest ghost and was dropped).

    Rows are aligned like a text diff: difflib matches the two row-signature
    sequences, and a mask row is cancelled only when BOTH sides of it are
    accounted for — its before-row matched somewhere in after (or is blank) AND
    its after-row matched somewhere in before (or is blank). Content that is new,
    removed, or edited in place stays unmatched on at least one side, so its rows
    survive. Merely moved content is matched on both sides and drops out.

    With no displacement this is a no-op by construction: rows equal at the same
    y produce an empty positional diff anyway, and changed rows never match.
    """
    sb, blank_b = _row_signatures(before)
    sa, blank_a = _row_signatures(after)
    sm = difflib.SequenceMatcher(None, sb, sa, autojunk=False)
    matched_b, matched_a = set(), set()
    for m in sm.get_matching_blocks():
        matched_b.update(range(m.a, m.a + m.size))
        matched_a.update(range(m.b, m.b + m.size))
    w, h = mask.size
    cancelled = [(y in matched_b or blank_b[y]) and (y in matched_a or blank_a[y])
                 for y in range(h)]
    if not any(cancelled):
        return mask
    out = mask.copy()
    d = ImageDraw.Draw(out)
    run = None
    for y in range(h + 1):
        inside = y < h and cancelled[y]
        if inside and run is None:
            run = y
        elif not inside and run is not None:
            d.rectangle([0, run, w, y - 1], fill=0)
            run = None
    return out


def diff_clusters(mask, cell=CELL, merge=MERGE_CELLS):
    """Connected components of the diff mask, heaviest first.

    getbbox() over the whole mask returns the union of everything that changed.
    Two unrelated changes — the edited label, and a relative timestamp that ticked
    over while the capture ran — then yield one box bracketing every untouched
    element between them, and a reviewer reads that as "these changed too".
    Components keep them apart.

    Grouping happens on a coarse grid so a word is one cluster and not one per
    glyph; the returned box is then re-measured on the full-resolution mask, so
    the grid groups but never inflates.
    """
    w, h = mask.size
    gw, gh = max(1, -(-w // cell)), max(1, -(-h // cell))
    small = mask.resize((gw, gh), Image.BOX)
    px = small.load()
    sx, sy = w / float(gw), h / float(gh)
    live = {(x, y) for y in range(gh) for x in range(gw) if px[x, y]}
    seen, out = set(), []
    for start in live:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx in range(-merge, merge + 1):
                for dy in range(-merge, merge + 1):
                    nb = (cx + dx, cy + dy)
                    if nb in live and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        xs, ys = [c[0] for c in comp], [c[1] for c in comp]
        coarse = (int(min(xs) * sx), int(min(ys) * sy),
                  min(w, int((max(xs) + 1) * sx) + 1), min(h, int((max(ys) + 1) * sy) + 1))
        exact = mask.crop(coarse).getbbox()
        if not exact:
            continue
        out.append((sum(px[x, y] for x, y in comp),
                    (coarse[0] + exact[0], coarse[1] + exact[1],
                     coarse[0] + exact[2], coarse[1] + exact[3])))
    out.sort(key=lambda t: -t[0])
    return out


def merge_boxes(boxes, gap=MERGE_PX):
    """Fuse expanded boxes that sit within the same region of the page.

    Clustering answers "which pixels changed together". This answers the different
    question "how many marks should a reviewer see". Seven rectangles down one
    redesigned panel is not seven findings, it is one; two marks four hundred
    pixels apart really are two. Merging is by edge distance, so boxes that touch
    or nearly touch collapse and distant ones stay separate.
    """
    out = list(boxes)
    fused = True
    while fused:
        fused = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if (max(0, max(a[0], b[0]) - min(a[2], b[2])) <= gap
                        and max(0, max(a[1], b[1]) - min(a[3], b[3])) <= gap):
                    out[i] = (min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3]))
                    del out[j]
                    fused = True
                    break
            if fused:
                break
    return sorted(out, key=lambda b: (b[1], b[0]))


def select_clusters(clusters, keep_ratio=KEEP_RATIO):
    """Split clusters into the ones worth marking and the ones that are noise.

    Kept by share of the heaviest cluster, not by absolute size: what counts as
    negligible depends on how big the real change is. The list is already sorted,
    so the kept set is a prefix.
    """
    if not clusters:
        return [], []
    cut = keep_ratio * clusters[0][0]
    keep = [c for c in clusters if c[0] >= cut]
    return keep, clusters[len(keep):]


def content_bottom(img, margin=24):
    """Last row carrying content, so a mostly-empty page does not dominate the crop.

    Dashboards are usually a short list on a tall viewport; keeping 500px of empty
    background shrinks the interesting part of every stacked variant.
    """
    w, h = img.size
    bg = img.getpixel((w - 4, h - 4))
    px = img.load()
    step = max(1, w // 240)
    for y in range(h - 1, -1, -1):
        for x in range(0, w, step):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 24:
                return min(h, y + margin)
    return h


def content_left(img, y0, y1, margin=8):
    """First column carrying content within a horizontal band.

    Bounds the focus crop. Cropping right of this cuts into the row itself, and
    the row label is the context the cropped variant exists to supply.
    """
    w, h = img.size
    bg = img.getpixel((w - 4, h - 4))
    px = img.load()
    y0, y1 = max(0, y0), min(h, y1)
    step = max(1, (y1 - y0) // 200)
    for x in range(w):
        for y in range(y0, y1, step):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 24:
                return max(0, x - margin)
    return 0


def pad_box(box, w, h, left=8, right=12, top=5, bottom=8):
    """Asymmetric on purpose: a symmetric box cuts into the line of text above."""
    return (max(0, box[0] - left), max(0, box[1] - top),
            min(w, box[2] + right), min(h, box[3] + bottom))


def quiet_cols(img, y0, y1, tol=12, noise=0.01):
    """Columns that are visually uniform across a horizontal band — the gutters
    between words, controls and the card edge.

    The column twin of quiet_rows, and needed for the same reason. The diff box
    covers only the pixels that actually changed, so on a reworded label the
    shared prefix stays outside it: "Contributors" -> "Contributions" differs from
    the tenth character on, and a box drawn on the raw diff starts in the middle of
    the word. Correct, and unreadable.
    """
    w, h = img.size
    y1 = min(h, y1)
    px = img.load()
    step = max(1, (y1 - y0) // 60)
    out = []
    for x in range(w):
        ref = px[x, y0]
        bad = n = 0
        for y in range(y0, y1, step):
            p = px[x, y]
            n += 1
            if abs(p[0] - ref[0]) + abs(p[1] - ref[1]) + abs(p[2] - ref[2]) > tol:
                bad += 1
        out.append(bad <= noise * n)
    return out


def edge_out(v, quiet, direction, limit, min_gap=1):
    """First uniform row/column outward from v that is at least min_gap wide.

    Deliberately not snap(): that rides a quiet run to its far end, which is what a
    crop edge wants and the opposite of what a highlight box wants. Riding the run
    would walk the box across the whole empty gutter and out to the page margin.

    The width test is what keeps the box off the inside of an element. A one-pixel
    quiet column also sits between two glyphs and inside the padding of a pill, so
    stopping at the first one cuts the pill in half just before its icon. Only a
    gap wide enough to separate two components ends the expansion.
    """
    n = len(quiet)
    for d in range(0, limit + 1):
        cand = v + direction * d
        if not (0 <= cand < n and quiet[cand]):
            continue
        if all(0 <= cand + direction * k < n and quiet[cand + direction * k]
               for k in range(min_gap)):
            return cand
    return max(0, min(n - 1, v))


def whole_lines(box, img, w, h, limit=260):
    """Grow the measured box out to the quiet gaps around it, so it brackets whole
    lines and whole words instead of the changed glyphs.

    What is highlighted stays measured — the box still comes from the pixel diff
    and is only expanded to the nearest visually empty row and column. Nothing is
    positioned by hand, and the expansion cannot wander onto an unchanged element:
    it stops at the first uniform gap in each direction.
    """
    rows = quiet_rows(img, box[0], box[2])
    top = edge_out(box[1], rows, -1, limit, ROW_GAP)
    bottom = edge_out(box[3], rows, +1, limit, ROW_GAP)
    cols = quiet_cols(img, top, bottom)
    left = edge_out(box[0], cols, -1, limit, COL_GAP)
    right = edge_out(box[2], cols, +1, limit, COL_GAP)
    return (max(0, left), max(0, top), min(w, right), min(h, bottom))


def quiet_rows(img, x0=0, x1=None, tol=12, noise=0.01):
    """Rows that are visually uniform — page gutters, list separators, padding.

    Crop edges are snapped to these. Slicing a screenshot at an arbitrary y cuts
    headings and table rows in half, which reads as a broken export and quietly
    undermines the proof it is supposed to carry.

    Restricted to [x0, x1) on purpose. Measured across the full width, a persistent
    left navigation makes nearly every row noisy and no cut line is ever found.
    """
    w, h = img.size
    x1 = w if x1 is None else x1
    px = img.load()
    step = max(1, (x1 - x0) // 200)
    out = []
    for y in range(h):
        ref = px[x0, y]
        bad = n = 0
        for x in range(x0, x1, step):
            p = px[x, y]
            n += 1
            if abs(p[0] - ref[0]) + abs(p[1] - ref[1]) + abs(p[2] - ref[2]) > tol:
                bad += 1
        out.append(bad <= noise * n)
    return out


def snap(y, quiet, direction, limit=150, edge_pull=90):
    """Move y outward to the nearest quiet row; give up quietly if there is none."""
    h = len(quiet)
    for d in range(0, limit + 1):
        cand = y + direction * d
        if 0 <= cand < h and quiet[cand]:
            # Ride the quiet run to its far end. The first quiet row is usually a
            # row's inner padding; stopping there cuts the separator off and the
            # last row reads as truncated. The end of the run is the real gap.
            while 0 <= cand + direction < h and quiet[cand + direction]:
                cand += direction
            # A crop that ends up hugging the page edge should just take the edge,
            # otherwise a sliver of header hangs above the label band.
            if direction < 0 and cand < edge_pull:
                return 0
            if direction > 0 and cand > h - edge_pull:
                return h
            return cand
    return max(0, min(h, y))


# --- drawing primitives ------------------------------------------------------
def as_boxes(box):
    """One box or a list of them — callers pass whichever the variant needs."""
    if not box:
        return []
    return [box] if isinstance(box[0], (int, float)) else list(box)


def boxed(img, color, box, width=3):
    im = img.copy()
    d = ImageDraw.Draw(im, "RGBA")
    for b in as_boxes(box):
        for grow, alpha in ((9, 26), (6, 34), (3, 46)):  # soft halo, readable on light UI
            d.rounded_rectangle([b[0] - grow, b[1] - grow, b[2] + grow, b[3] + grow],
                                radius=7 + grow, outline=color + (alpha,), width=3)
        d.rounded_rectangle(b, radius=6, outline=color, width=width)
    return im


def band(width, text, sub, color, h=BAND_H, fs=BAND_FS):
    b = Image.new("RGB", (width, h), (255, 255, 255))
    d = ImageDraw.Draw(b)
    d.rectangle([0, 0, 8, h], fill=color)
    d.text((26, (h - fs) // 2 - 3), text, font=font(fs), fill=color)
    if sub:
        d.text((26 + d.textlength(text, font=font(fs)) + 16, (h - fs) // 2 + 2),
               sub, font=font(17, bold=False), fill=MUTED)
    d.line([(0, h - 1), (width, h - 1)], fill=(229, 231, 235))
    return b


def stack(parts, gap=0, bg=(255, 255, 255)):
    w = max(p.width for p in parts)
    out = Image.new("RGB", (w, sum(p.height for p in parts) + gap * (len(parts) - 1)), bg)
    y = 0
    for p in parts:
        out.paste(p, ((w - p.width) // 2, y))
        y += p.height + gap
    return out


def side(parts, gap=0, bg=(255, 255, 255)):
    h = max(p.height for p in parts)
    out = Image.new("RGB", (sum(p.width for p in parts) + gap * (len(parts) - 1), h), bg)
    x = 0
    for p in parts:
        out.paste(p, (x, (h - p.height) // 2))
        x += p.width + gap
    return out


def frame(img, pad=24, bg=PAGE_BG):
    out = Image.new("RGB", (img.width + 2 * pad, img.height + 2 * pad), bg)
    out.paste(img, (pad, pad))
    return out


def shift(box, crop, scale=1):
    return tuple(v * scale for v in (box[0] - crop[0], box[1] - crop[1],
                                     box[2] - crop[0], box[3] - crop[1]))


def shift_all(boxes, crop, scale=1):
    return [shift(b, crop, scale) for b in as_boxes(boxes)]


def labelled_pair(b_img, a_img, box, sub_b="", sub_a="", gap=24, pad=24, fs=BAND_FS, width=3):
    b = stack([band(b_img.width, LBL_BEFORE, sub_b, RED, fs=fs),
               boxed(b_img, RED, box, width) if box else b_img])
    a = stack([band(a_img.width, LBL_AFTER, sub_a, GREEN, fs=fs),
               boxed(a_img, GREEN, box, width) if box else a_img])
    return frame(stack([b, a], gap=gap), pad=pad)


# --- variants ----------------------------------------------------------------
def build(issue_dir):
    bpath = os.path.join(issue_dir, "before", "screenshot.png")
    apath = os.path.join(issue_dir, "after", "screenshot.png")
    for p in (bpath, apath):
        if not os.path.isfile(p):
            print(f"variants skipped: missing {os.path.relpath(p, issue_dir)}", file=sys.stderr)
            return 4

    before = Image.open(bpath).convert("RGB")
    after = Image.open(apath).convert("RGB")
    if before.size != after.size:
        print(f"variants skipped: size mismatch {before.size} vs {after.size}", file=sys.stderr)
        return 4

    w, h = before.size
    raw = diff_mask(before, after)
    if raw.getbbox() is None:
        print("variants skipped: before and after are pixel-identical — nothing to mark",
              file=sys.stderr)
        return 4

    mask = cancel_displacement(raw, before, after)
    if mask.getbbox() is None:
        # Everything that differs is content that merely moved. That is not a
        # reason to produce nothing: "more space between the rows" and "this panel
        # moved" are real, visible changes. Cancellation exists to stop the shift
        # from drowning an edit, so when the shift is ALL there is, mark it.
        print("variants: the change is pure repositioning — no inserted, removed or "
              "edited content, so the marks show what moved", file=sys.stderr)
        mask = raw

    keep, dropped = select_clusters(diff_clusters(mask))
    # One expanded box per surviving cluster, then fused into regions. Drawn
    # separately, so two changes at opposite ends of the page mark both spots
    # instead of everything in between.
    grown = [pad_box(whole_lines(pad_box(b, w, h), after, w, h), w, h, 4, 4, 3, 3)
             for _, b in keep]
    boxes = merge_boxes(grown)
    # Said out loud, never silently: a dropped cluster is a real pixel change the
    # reviewer is not being shown, and a wrongly dropped one would otherwise be an
    # invisible bug. Only report the ones no drawn box covers — a light cluster
    # inside the marked region is already on screen.
    loose = [b for _, b in dropped
             if not any(m[0] <= b[0] and m[1] <= b[1] and m[2] >= b[2] and m[3] >= b[3]
                        for m in boxes)]
    if loose:
        print("variants: %d changed region(s) left unmarked, each under %d%% of the main "
              "change (%s)" % (len(loose), int(KEEP_RATIO * 100),
                               "; ".join("%dx%d at %d,%d" % (b[2] - b[0], b[3] - b[1], b[0], b[1])
                                         for b in loose[:4])), file=sys.stderr)
    # The union drives crop geometry only; it is never what gets drawn.
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
           max(b[2] for b in boxes), max(b[3] for b in boxes))
    # The zoom variants are about one thing: the region holding the heaviest cluster.
    main = next(b for b in boxes
                if b[0] <= grown[0][0] and b[1] <= grown[0][1]
                and b[2] >= grown[0][2] and b[3] >= grown[0][3])
    area_ratio = ((box[2] - box[0]) * (box[3] - box[1])) / float(w * h)
    # Focus column: from just left of the change to the right edge, so the crop
    # keeps the full row including its trailing action buttons. Clamped to real
    # content further down — see the card crop.
    fx0 = max(0, box[0] - 140)
    quiet = quiet_rows(after, fx0, w)
    # A change covering most of the page is a redesign, not a marked spot; the zoom
    # crops would be meaningless, so those are skipped and it is said out loud.
    wide_change = area_ratio > 0.45

    out_dir = os.path.join(issue_dir, "deliverable", "variants")
    os.makedirs(out_dir, exist_ok=True)
    written = []

    def save(name, img):
        img.save(os.path.join(out_dir, name))
        written.append(f"deliverable/variants/{name}")

    # 01 — whole page, stacked, empty tail trimmed. The one that always ships:
    # it is the only variant that still shows toasts, tabs and surrounding state.
    page = (0, 0, w, max(content_bottom(after), box[3] + 60))
    save("01-full-view.png",
         labelled_pair(before.crop(page), after.crop(page), shift_all(boxes, page)))

    if not wide_change:
        # 02 — the change plus enough neighbours to compare against. Vertical
        # context scales with the change so a tall diff is not cropped in half.
        # Capped at ~3x the change: without a ceiling a tall box turns the context
        # view into a second full-page shot, which 01 already is.
        ctx_v = min(max(200, 8 * (box[3] - box[1])), 3 * (box[3] - box[1]) + 200)
        top = snap(max(0, box[1] - ctx_v), quiet, -1)
        bottom = min(page[3], snap(min(h, box[3] + ctx_v), quiet, +1))
        # The 140px focus offset is meant to drop empty left margin. On a page
        # whose content starts near x=0 it instead amputates the row label, so it
        # is clamped to the leftmost pixel that actually carries something.
        card = (min(fx0, content_left(after, top, bottom)), top, w, bottom)
        b_card, a_card = before.crop(card), after.crop(card)
        save("02-context.png", labelled_pair(b_card, a_card, shift_all(boxes, card)))

        # 03 — the changed line alone, magnified. Smallest file, largest type.
        # No vertical padding: whole_lines() already put box[1]/box[3] on the
        # quiet rows bounding the changed line, so any extra would cross back
        # into the neighbouring row and show a sliver of it.
        #
        # Horizontally, 60px of lead-in is wanted only when it is empty space. Take
        # it by walking INWARD from box[0]-60 to the first uniform column, so the
        # cut lands in a gutter instead of slicing a glyph or a neighbouring column.
        zcols = quiet_cols(after, main[1], main[3])
        tight = (edge_out(max(0, main[0] - 60), zcols, +1, 60, COL_GAP), main[1],
                 min(w, main[2] + 300), main[3])
        scale = max(1, min(4, round(1400 / max(1, tight[2] - tight[0]))))
        b_z, a_z = (i.crop(tight).resize(((tight[2] - tight[0]) * scale,
                                          (tight[3] - tight[1]) * scale), Image.LANCZOS)
                    for i in (before, after))
        save("03-line-zoom.png",
             labelled_pair(b_z, a_z, shift(main, tight, scale), gap=20, pad=28, fs=30, width=4))

        # 05 — context plus a 3x readout of the line, for when the marked text is
        # too small to read at page scale but the surrounding state still matters.
        zs = 3
        lens = (max(0, main[0] - 4), main[1], min(w, main[2] + 4), main[3])
        b_l, a_l = (i.crop(lens).resize(((lens[2] - lens[0]) * zs, (lens[3] - lens[1]) * zs),
                                        Image.LANCZOS) for i in (before, after))

        def callout(img, text, color):
            inner = Image.new("RGB", (img.width + 8, img.height + 8), color)
            inner.paste(img, (4, 4))
            cap = Image.new("RGB", (inner.width, 42), (255, 255, 255))
            ImageDraw.Draw(cap).text((2, 8), text, font=font(26), fill=color)
            return stack([cap, inner])

        top = labelled_pair(b_card, a_card, shift_all(boxes, card), pad=0)
        strip = frame(stack([callout(b_l, LBL_BEFORE, RED),
                             callout(a_l, LBL_AFTER, GREEN)], gap=20),
                      pad=18, bg=(255, 255, 255))
        if strip.width > top.width:
            strip = strip.resize((top.width, round(strip.height * top.width / strip.width)),
                                 Image.LANCZOS)
        save("05-context-with-zoom.png", frame(stack([top, strip], gap=20), pad=24))

        # 04 — blink comparator. Two stills alternating in place beat any
        # side-by-side: the eye detects motion where it cannot detect difference.
        # Renders animated inline in most issue-tracker comments.
        frames = []
        for img, color, label in ((b_card, RED, LBL_BEFORE), (a_card, GREEN, LBL_AFTER)):
            f_img = stack([band(img.width, label, "", color, h=54, fs=24),
                           boxed(img, color, shift_all(boxes, card))])
            if f_img.width > 1200:
                f_img = f_img.resize((1200, round(f_img.height * 1200 / f_img.width)),
                                     Image.LANCZOS)
            frames.append(f_img.convert("P", palette=Image.ADAPTIVE, colors=128))
        frames[0].save(os.path.join(out_dir, "04-blink.gif"), save_all=True,
                       append_images=frames[1:], duration=1100, loop=0, optimize=True)
        written.append("deliverable/variants/04-blink.gif")
    else:
        print(f"variants: change covers {area_ratio:.0%} of the page — "
              "zoom/blink variants skipped, only 01-full-view.png makes sense", file=sys.stderr)

    for rel in written:
        print(rel)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: variants.py <issue_dir>", file=sys.stderr)
        sys.exit(2)
    # Defense in depth behind phase.sh's regex: this process writes PNGs into the
    # directory it is handed, so it refuses any path outside the capture root.
    root = os.path.realpath(os.path.join(os.environ.get("POF_ROOT", os.getcwd()), ".proof-of-fix"))
    target = os.path.realpath(sys.argv[1])
    if os.path.commonpath([root, target]) != root or target == root:
        print(f"variants refused: {target} is outside {root}", file=sys.stderr)
        sys.exit(2)
    sys.exit(build(target))
