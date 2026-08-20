"""
jcns_mapping.py
---------------
The three-point piecewise transfer function, evaluated numerically.

The same curve is expressed twice in this addon: once as a Python string that
becomes a Blender SCRIPTED driver (jcns_operators._build_piecewise_expr), and
once here as a plain function used by the UI for read-outs and the curve
preview.  Those two MUST agree — a panel that reports a different number from
what the driver actually produces is worse than no panel at all, so
tests/mapping_test.py evaluates the generated expression and compares it against
this module across a grid of inputs.

Kept free of `bpy` so it can be tested standalone.

Anchors, all in the file's own units (degrees for rotation):

    A = (from_start, to_start)   first endpoint
    B = (from_kink,  to_kink)    slope change
    C = (from_end,   to_end)     second endpoint

Segment 1 maps [A.x -> B.x] onto [A.y -> B.y]; segment 2 maps [B.x -> C.x] onto
[B.y -> C.y].  Outside the covered range the output is clamped to the nearer
segment's bound.

TWO CURVE MODES (measured in-game 2026-08-21 against a purpose-built rig).  The
per-source byte at +24 — which jcns_parser calls `UpdateTiming`, a misnomer —
selects the curve shape:

    +24 == 0, 1  ->  TWO-POINT: the kink is ignored entirely; the output is the
                     straight line A -> C.
    +24 == 2, 3  ->  THREE-POINT: the piecewise curve described above.

Within each pair the members were bit-identical across every geometry tested, so
the split is exactly bit 1 (0x02).

Proof: two sources with identical geometry and identical +25, differing only in
+24, produced respectively an exact straight line and an exact piecewise curve
(every binned sample matched its model to 0.01 deg, and local slopes were
constant to four decimals within each segment — so the segments are strictly
linear, not eased).  The constraint-level `Flags` bit 0 was independently shown
to have no effect.  The byte at +25 shifts the sampled input quantity slightly
but does NOT change the curve shape.

Degenerate anchors, measured separately in each mode:

    two-point    collapsing either segment changes nothing (it is a straight
                 line regardless); all three `from` equal -> flat 0.
    three-point  start == kink -> segment 2 governs, `to_start` ignored;
                 kink == end   -> segment 1 governs, `to_end` ignored;
                 all three equal -> steps at the kink between `to_start` and
                 `to_end`, and `to_kink` never appears.
                 A descending `from` range behaves exactly like the equivalent
                 ascending one.
                 A kink lying strictly OUTSIDE [start, end] kills the source:
                 the output is a flat 0, not a clamped constant.  Measured over
                 three such geometries, including a pair differing only in
                 `to_start` by 90 deg that produced identical flat zeros.

UNTESTED: +24 == 4 / 5 (9 sources in the whole corpus).
"""


def is_two_point(update_timing):
    """Does this source use the two-point (straight line A -> C) curve mode?

    The per-source byte at +24 — stored under the file-format name
    `UpdateTiming` — is really the curve-mode selector.  Measured in-game
    2026-08-21 over five degenerate and two non-degenerate geometries:
    0 means two-point, every other observed value (2 and 3) means three-point,
    and 2 and 3 are bit-identical in every case tested.

    Measured: 0 and 1 are two-point, 2 and 3 are three-point.  That split is
    exactly bit 1 (0x02), which is probably the real encoding, but 4 and 5 (9
    sources in the whole corpus) were never measured, so this sticks to the
    values actually observed rather than extrapolating the bit reading.
    """
    return update_timing in (0, 1)


def eval_piecewise(from_start, from_kink, from_end,
                   to_start, to_kink, to_end, x, two_point=False):
    """Output of the transfer function for a source value of `x`.

    `two_point=True` selects the +24 == 0 curve mode (straight line A -> C).
    """
    span1 = from_kink - from_start
    span2 = from_end - from_kink
    total = from_end - from_start

    def seg(x0, y0, x1, y1, span):
        k = (y1 - y0) / span
        lo, hi = min(y0, y1), max(y0, y1)
        return max(lo, min(hi, y0 + (x - x0) * k))

    if two_point:
        if abs(total) < 1e-9:
            return 0.0
        return seg(from_start, to_start, from_end, to_end, total)

    # Three-point mode.  A kink strictly outside the [start, end] span kills the
    # whole source — measured over three such geometries (kink past the end, kink
    # before the start, and the same with a wildly different to_start), all of
    # which produced a flat 0 while neighbouring slots evaluated normally.
    lo, hi = (from_start, from_end) if from_start <= from_end else (from_end, from_start)
    if from_kink < lo - 1e-9 or from_kink > hi + 1e-9:
        return 0.0

    # Degenerate handling below was measured in-game (Round 12) and matches the
    # original heuristics, except for the fully-collapsed case which steps
    # between to_start and to_end.
    if abs(span1) < 1e-9 and abs(span2) < 1e-9:
        # Measured: x <= kink -> to_start, x > kink -> to_end.  to_kink never
        # appears (a probe with to=(5, 33, 7) only ever produced 5 and 7).
        return to_start if x <= from_kink else to_end
    if abs(span2) < 1e-9:                      # only segment 1 is live
        return seg(from_start, to_start, from_kink, to_kink, span1)
    if abs(span1) < 1e-9:                      # only segment 2 is live
        return seg(from_kink, to_kink, from_end, to_end, span2)

    # Pick the segment by which side of the kink `x` falls on, honouring a
    # descending MapFrom range (from_start > from_end) just like the driver does.
    on_first = (x <= from_kink) if from_start <= from_end else (x >= from_kink)
    if on_first:
        return seg(from_start, to_start, from_kink, to_kink, span1)
    return seg(from_kink, to_kink, from_end, to_end, span2)


def rest_position(from_start, from_kink, from_end):
    """Where the rest pose (source == 0) sits relative to the MapFrom anchors.

    Returned as one of 'A', 'B', 'C', 'inside', 'outside'.  This matters because
    whichever anchor the rest pose coincides with is the one whose MapTo value
    the bone will be holding when nothing is moving.
    """
    if abs(from_start) < 1e-6:
        return 'A'
    if abs(from_end) < 1e-6:
        return 'C'
    if abs(from_kink) < 1e-6:
        return 'B'
    lo, hi = min(from_start, from_end), max(from_start, from_end)
    return 'inside' if lo < 0.0 < hi else 'outside'


def describe(source):
    """
    Diagnose one source mapping.

    `source` is anything exposing from_start/from_kink/from_end/to_start/
    to_kink/to_end — a parser dict or a JCNSSourceProperties instance.

    Returns a dict with the output at the three anchors plus at rest, where the
    rest pose sits, and whether the constraint does anything at all.
    """
    def g(name):
        if isinstance(source, dict):
            return float(source.get(name, 0.0) or 0.0)
        return float(getattr(source, name, 0.0))

    fs, fk, fe = g('from_start'), g('from_kink'), g('from_end')
    ts, tk, te = g('to_start'), g('to_kink'), g('to_end')

    at_rest = eval_piecewise(fs, fk, fe, ts, tk, te, 0.0)
    return {
        'at_rest':    at_rest,
        'at_start':   eval_piecewise(fs, fk, fe, ts, tk, te, fs),
        'at_kink':    eval_piecewise(fs, fk, fe, ts, tk, te, fk),
        'at_end':     eval_piecewise(fs, fk, fe, ts, tk, te, fe),
        'rest_pos':   rest_position(fs, fk, fe),
        # A non-zero output at rest means the bone is deflected before anything
        # has moved.  Legitimate for some setups, but almost always a mistake
        # when editing by hand, so the UI flags it.
        'offset_at_rest': abs(at_rest) > 1e-4,
        # All three MapTo anchors zero: the constraint can never produce output.
        'inert': abs(ts) < 1e-9 and abs(tk) < 1e-9 and abs(te) < 1e-9,
        'from': (fs, fk, fe),
        'to':   (ts, tk, te),
    }


def describe_channel(sources):
    """Diagnose a driven channel from the sources of the constraint that owns it.

    A single source can look harmless while the total is still deflected at rest,
    so the per-source read-out is not sufficient on its own.

    Pass only the live constraint's sources: where several constraints target one
    channel the engine keeps the last and drops the rest, so folding all of them
    in here would report a rest deflection that never actually happens.
    """
    infos = [describe(s) for s in sources]
    at_rest = sum(i['at_rest'] for i in infos)
    return {
        'at_rest': at_rest,
        'offset_at_rest': abs(at_rest) > 1e-4,
        'n_sources': len(infos),
        'all_inert': bool(infos) and all(i['inert'] for i in infos),
        'sources': infos,
    }


def would_swapping_ends_help(source):
    """True when exchanging to_start and to_end removes a rest-pose deflection.

    The common authoring slip: MapFrom runs downwards (e.g. [-60, -15, 0]) so the
    rest pose coincides with anchor C, but the intended deflection was written
    into to_end — the anchor that applies while the rig is idle — instead of
    to_start.  Swapping the two puts it back on the moving end.
    """
    info = describe(source)
    if not info['offset_at_rest']:
        return False
    fs, fk, fe = info['from']
    ts, tk, te = info['to']
    swapped = {'from_start': fs, 'from_kink': fk, 'from_end': fe,
               'to_start': te, 'to_kink': tk, 'to_end': ts}
    return not describe(swapped)['offset_at_rest']


def plain_description(source, unit="°"):
    """Describe the mapping the way it is actually reasoned about.

    The file stores three anchors in its own order (A -> B -> C), but the rest
    pose does not have to be anchor A — for a descending range like
    [-60, -15, 0] it sits on anchor C, so reading the numbers left to right
    describes the motion backwards.

    This walks outward from the rest pose instead, in each direction the source
    bone can actually travel, and reports what the target does over each leg.

    Returns a dict:
        rest_output   output while nothing is moving
        legs          [{'direction': +1/-1, 'steps': [(from, to, out0, out1, kind)]}]
                      kind is 'dead' (no response) or 'move'
        inert         True when the mapping can never produce output
    """
    def g(name):
        if isinstance(source, dict):
            return float(source.get(name, 0.0) or 0.0)
        return float(getattr(source, name, 0.0))

    fs, fk, fe = g('from_start'), g('from_kink'), g('from_end')
    ts, tk, te = g('to_start'), g('to_kink'), g('to_end')

    at_rest = eval_piecewise(fs, fk, fe, ts, tk, te, 0.0)
    inert = abs(ts) < 1e-9 and abs(tk) < 1e-9 and abs(te) < 1e-9

    # The three anchors do not have to be ordered, and when they double back
    # (say [-120, 0, -30]) one of them becomes unreachable: the transfer
    # function picks a segment by which side of the kink the input is on, so a
    # segment lying on the same side as its neighbour is never evaluated.
    #
    # Describing the curve by sorting the anchors and joining the dots would
    # therefore report behaviour the rig does not have.  Sample the real
    # function instead and read the shape back off it, so this can only ever
    # describe what eval_piecewise — and hence the driver — actually does.
    lo, hi = min(0.0, fs, fk, fe), max(0.0, fs, fk, fe)
    if hi - lo < 1e-9:
        return {'rest_output': at_rest, 'legs': [], 'inert': inert,
                'offset_at_rest': abs(at_rest) > 1e-4,
                'anchors_ordered': True, 'unreachable_anchor': None}

    n = 401
    step = (hi - lo) / (n - 1)
    pts = [(lo + i * step, eval_piecewise(fs, fk, fe, ts, tk, te, lo + i * step))
           for i in range(n)]

    # Merge samples into straight runs; a slope change starts a new run.
    runs = []
    i = 0
    while i < len(pts) - 1:
        x0, y0 = pts[i]
        j = i + 1
        slope = (pts[j][1] - y0) / (pts[j][0] - x0)
        while j < len(pts) - 1:
            nxt = (pts[j + 1][1] - y0) / (pts[j + 1][0] - x0)
            if abs(nxt - slope) > 1e-4 * max(1.0, abs(slope)):
                break
            slope = nxt
            j += 1
        runs.append((x0, pts[j][0], y0, pts[j][1]))
        i = j

    # Sampling puts a breakpoint on the nearest sample rather than exactly on
    # the anchor, so 25 would be reported as 24.9.  Snap back to the real value.
    def snap(x):
        for a in (0.0, fs, fk, fe):
            if abs(x - a) <= step * 1.5:
                return a
        return x

    runs = [(snap(x0), snap(x1),
             eval_piecewise(fs, fk, fe, ts, tk, te, snap(x0)),
             eval_piecewise(fs, fk, fe, ts, tk, te, snap(x1)))
            for x0, x1, y0, y1 in runs]

    legs = []
    for direction in (+1, -1):
        steps = []
        ordered = runs if direction > 0 else list(reversed(runs))
        for x0, x1, y0, y1 in ordered:
            a, b = (x0, x1) if direction > 0 else (x1, x0)
            u, v = (y0, y1) if direction > 0 else (y1, y0)
            if (b <= 1e-6) if direction > 0 else (b >= -1e-6):
                continue                       # entirely on the other side
            a = max(a, 0.0) if direction > 0 else min(a, 0.0)
            if abs(b - a) < 1e-6:
                continue                       # zero-width run at a breakpoint
            u = eval_piecewise(fs, fk, fe, ts, tk, te, a)
            kind = 'dead' if abs(v - u) < 1e-4 else 'move'
            if steps and steps[-1][4] == kind == 'dead':
                steps[-1] = (steps[-1][0], b, steps[-1][2], v, 'dead')
            else:
                steps.append((a, b, u, v, kind))
        if steps:
            legs.append({'direction': direction, 'steps': steps})

    # Which anchor, if any, the function never reaches.
    unreachable = None
    ordered_anchors = (fs <= fk <= fe) or (fs >= fk >= fe)
    if not ordered_anchors:
        for name, ax, ay in (('A', fs, ts), ('B', fk, tk), ('C', fe, te)):
            if abs(eval_piecewise(fs, fk, fe, ts, tk, te, ax) - ay) > 1e-4:
                unreachable = name
                break

    return {'rest_output': at_rest, 'legs': legs, 'inert': inert,
            'offset_at_rest': abs(at_rest) > 1e-4,
            'anchors_ordered': ordered_anchors,
            'unreachable_anchor': unreachable}


def sample(source, n=48):
    """Sample the curve across its MapFrom span; returns [(x, y), …] for plotting."""
    def g(name):
        if isinstance(source, dict):
            return float(source.get(name, 0.0) or 0.0)
        return float(getattr(source, name, 0.0))

    fs, fk, fe = g('from_start'), g('from_kink'), g('from_end')
    ts, tk, te = g('to_start'), g('to_kink'), g('to_end')

    lo, hi = min(fs, fe, 0.0), max(fs, fe, 0.0)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / float(n - 1)
    return [(lo + i * step,
             eval_piecewise(fs, fk, fe, ts, tk, te, lo + i * step))
            for i in range(n)]
