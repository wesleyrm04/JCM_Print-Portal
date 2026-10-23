"""Photo layout maths.  static/js/layout.js is a line-for-line port of this file -
tests/test_layout.py runs both and checks they agree, so the on-screen preview, the
generated PDF and the server-side page counts can never drift apart."""
import math

EPS = 1e-9


def _fixed(cell_w, cell_h, pw, ph, margin, gap):
    ax, ay = pw - 2 * margin, ph - 2 * margin
    cols = math.floor((ax + gap) / (cell_w + gap) + EPS)
    rows = math.floor((ay + gap) / (cell_h + gap) + EPS)
    return max(cols, 0), max(rows, 0)


def layout_rects(layout: dict, pw: float, ph: float, margin: float, gap: float):
    """Return (rects, cols, rows) for one page. rects = [(x, y, w, h), ...] in mm."""
    t = layout.get("type", "fixed")
    if t == "full":
        return [(margin, margin, pw - 2 * margin, ph - 2 * margin)], 1, 1

    if t == "grid":
        cols, rows = int(layout["cols"]), int(layout["rows"])
        cw = (pw - 2 * margin - (cols - 1) * gap) / cols
        ch = (ph - 2 * margin - (rows - 1) * gap) / rows
        rects = [(margin + c * (cw + gap), margin + r * (ch + gap), cw, ch)
                 for r in range(rows) for c in range(cols)]
        return rects, cols, rows

    # fixed physical size: try portrait and landscape, keep whichever fits more
    w, h = float(layout["w"]), float(layout["h"])
    best = None
    for m in (margin, 0.0):                       # large prints (e.g. 8R on A4) may need 0 margin
        for (cw, ch) in ((w, h), (h, w)):
            cols, rows = _fixed(cw, ch, pw, ph, m, gap)
            n = cols * rows
            if n > 0 and (best is None or n > best[0]):
                best = (n, cw, ch, cols, rows)
        if best:
            break
    if not best:                                  # bigger than the paper: one shrunken cell
        return [(margin, margin, pw - 2 * margin, ph - 2 * margin)], 1, 1
    _, cw, ch, cols, rows = best
    total_w = cols * cw + (cols - 1) * gap
    total_h = rows * ch + (rows - 1) * gap
    x0, y0 = (pw - total_w) / 2, (ph - total_h) / 2
    rects = [(x0 + c * (cw + gap), y0 + r * (ch + gap), cw, ch)
             for r in range(rows) for c in range(cols)]
    return rects, cols, rows


def per_page(layout, pw, ph, margin, gap) -> int:
    return len(layout_rects(layout, pw, ph, margin, gap)[0])


def find_layout(cfg: dict, key: str):
    for lay in cfg["layouts"]:
        if lay["key"] == key:
            return lay
    return None
