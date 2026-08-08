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
"""


def eval_piecewise(from_start, from_kink, from_end,
                   to_start, to_kink, to_end, x):
    """Output of the transfer function for a source value of `x`."""
    span1 = from_kink - from_start
    span2 = from_end - from_kink

    # Both segments collapsed: constant output.
    if abs(span1) < 1e-9 and abs(span2) < 1e-9:
        return to_start

    def seg(x0, y0, x1, y1, span):
        k = (y1 - y0) / span
        lo, hi = min(y0, y1), max(y0, y1)
        return max(lo, min(hi, y0 + (x - x0) * k))

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


def combine_values(values, mode):
    """Fold several mapped outputs into the one value a channel produces."""
    if not values:
        return 0.0
    if mode == 'FIRST':
        return values[0]
    if mode == 'MAX':
        return max(values)
    if mode == 'MIN':
        return min(values)
    if mode == 'AVERAGE':
        return sum(values) / float(len(values))
    return sum(values)


def describe_channel(sources, combine='SUM'):
    """Diagnose a whole driven channel — every source of every constraint on it.

    A single source can look harmless while the channel total is still deflected
    at rest, so the per-source read-out is not sufficient on its own.
    """
    infos = [describe(s) for s in sources]
    at_rest = combine_values([i['at_rest'] for i in infos], combine)
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

    # Anchors as (input, output), de-duplicated and ordered by input.
    anchors = sorted({round(x, 6): y for x, y in
                      ((fs, ts), (fk, tk), (fe, te))}.items())

    legs = []
    for direction in (+1, -1):
        beyond = [(x, y) for x, y in anchors
                  if (x > 1e-6 if direction > 0 else x < -1e-6)]
        if not beyond:
            continue
        if direction < 0:
            beyond = list(reversed(beyond))          # walk away from zero
        steps = []
        prev_x, prev_y = 0.0, at_rest
        for x, y in beyond:
            kind = 'dead' if abs(y - prev_y) < 1e-6 else 'move'
            steps.append((prev_x, x, prev_y, y, kind))
            prev_x, prev_y = x, y
        # collapse consecutive legs that behave identically
        merged = []
        for st in steps:
            if merged and merged[-1][4] == st[4] == 'dead':
                merged[-1] = (merged[-1][0], st[1], merged[-1][2], st[3], 'dead')
            else:
                merged.append(st)
        legs.append({'direction': direction, 'steps': merged})

    return {'rest_output': at_rest, 'legs': legs, 'inert': inert,
            'offset_at_rest': abs(at_rest) > 1e-4}


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
