"""
jcns_curve.py
-------------
Rasterise the piecewise transfer function into an RGBA buffer.

Blender panels have no chart widget, so the curve is drawn into a pixel buffer
and shown through `layout.template_icon()` with a preview created by
bpy.utils.previews.  Keeping the rasteriser here — free of `bpy` — means the
geometry can be unit-tested without Blender, and the UI layer only has to own
the preview datablock and its cache key.

Pixel order matches what Blender expects from ImagePreview.image_pixels_float:
a flat RGBA float list, row 0 at the BOTTOM.
"""

from jcns_mapping import eval_piecewise, describe


# Colours chosen to read on Blender's dark panel background.
BG        = (0.13, 0.13, 0.13, 1.00)
GRID      = (0.26, 0.26, 0.26, 1.00)
AXIS      = (0.40, 0.40, 0.40, 1.00)
CURVE     = (0.35, 0.72, 1.00, 1.00)
CURVE_BAD = (1.00, 0.42, 0.35, 1.00)
KINK      = (1.00, 0.78, 0.28, 1.00)
REST_OK   = (0.45, 0.90, 0.50, 1.00)
REST_BAD  = (1.00, 0.30, 0.25, 1.00)


class Canvas:
    def __init__(self, w, h, bg=BG):
        self.w, self.h = w, h
        self.px = [list(bg) for _ in range(w * h)]

    def put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = list(c)

    def hline(self, y, c):
        for x in range(self.w):
            self.put(x, y, c)

    def vline(self, x, c):
        for y in range(self.h):
            self.put(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        """Integer DDA — thin, no anti-aliasing, which suits a small icon."""
        dx, dy = x1 - x0, y1 - y0
        steps = max(abs(dx), abs(dy))
        if steps == 0:
            self.put(x0, y0, c)
            return
        for i in range(steps + 1):
            t = i / steps
            self.put(int(round(x0 + dx * t)), int(round(y0 + dy * t)), c)

    def disc(self, cx, cy, r, c):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.put(x, y, c)

    def flat(self):
        out = []
        for p in self.px:
            out.extend(p)
        return out


def _bounds(sources):
    """Input and output ranges covering every source, always including zero."""
    xs, ys = [0.0], [0.0]
    for s in sources:
        d = describe(s)
        xs.extend(d['from'])
        ys.extend([d['at_start'], d['at_kink'], d['at_end'], d['at_rest']])
        ys.extend(d['to'])
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 < 1e-6:
        x0, x1 = x0 - 1.0, x1 + 1.0
    if y1 - y0 < 1e-6:
        y0, y1 = y0 - 1.0, y1 + 1.0
    px, py = (x1 - x0) * 0.08, (y1 - y0) * 0.12
    return x0 - px, x1 + px, y0 - py, y1 + py


def render(sources, width=180, height=110, samples=None):
    """Draw every source's curve. `sources` may be dicts or property groups.

    Returns (flat_rgba_list, info) where info carries the plotted bounds and
    whether any source is deflected at rest, so the caller can label the axes
    without recomputing.
    """
    sources = list(sources)
    if not sources:
        return Canvas(width, height).flat(), {'empty': True}

    x0, x1, y0, y1 = _bounds(sources)
    cv = Canvas(width, height)
    samples = samples or width

    def sx(v):
        return int(round((v - x0) / (x1 - x0) * (width - 1)))

    def sy(v):
        return int(round((v - y0) / (y1 - y0) * (height - 1)))

    # zero lines first so the curve draws over them
    if y0 <= 0.0 <= y1:
        cv.hline(sy(0.0), AXIS)
    if x0 <= 0.0 <= x1:
        cv.vline(sx(0.0), AXIS)

    any_offset = False
    for s in sources:
        d = describe(s)
        offset = d['offset_at_rest']
        any_offset = any_offset or offset
        colour = CURVE_BAD if offset else CURVE
        fs, fk, fe = d['from']
        ts, tk, te = d['to']

        prev = None
        for i in range(samples):
            xv = x0 + (x1 - x0) * i / float(samples - 1)
            yv = eval_piecewise(fs, fk, fe, ts, tk, te, xv)
            cur = (sx(xv), sy(yv))
            if prev is not None:
                cv.line(prev[0], prev[1], cur[0], cur[1], colour)
            prev = cur

        # the kink is the anchor people actually tune, so mark it
        if x0 <= fk <= x1:
            cv.disc(sx(fk), sy(d['at_kink']), 1, KINK)

        # and the rest pose, which is what the read-out warns about
        if x0 <= 0.0 <= x1:
            cv.disc(sx(0.0), sy(d['at_rest']), 2,
                    REST_BAD if offset else REST_OK)

    return cv.flat(), {
        'empty': False,
        'x_min': x0, 'x_max': x1, 'y_min': y0, 'y_max': y1,
        'offset_at_rest': any_offset,
        'n_sources': len(sources),
    }


def cache_key(sources, width, height):
    """Everything the rendering depends on, so the UI can skip redundant work.

    Panels redraw constantly; re-rasterising on every draw would be wasteful.
    """
    parts = [width, height]
    for s in sources:
        for name in ('from_start', 'from_kink', 'from_end',
                     'to_start', 'to_kink', 'to_end'):
            v = (s.get(name, 0.0) if isinstance(s, dict)
                 else getattr(s, name, 0.0))
            parts.append(round(float(v), 4))
    return tuple(parts)
