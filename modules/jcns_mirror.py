"""
jcns_mirror.py
--------------
Mirror a constraint mapping from one side of a rig to the other.

A mirrored pair relates as

    f_R(x) = out_sign * f_L(in_sign * x)

and both signs come from the two bones' local frames, read off the armature.
A reflection reverses handedness, so a rotation of +t about a world axis u
becomes -t about M*u: if the right bone's local axis equals +M*u the mirrored
angle is -t, and if it equals -M*u the two negations cancel.  Measured against
an unmodified game skeleton this predicts the SOURCE sign for 80 of 80 pairs.

The output side additionally depends on WHAT is being driven:

  rotation      a pseudovector — mirrors as sigma says
  translation   an ordinary vector — mirrors the opposite way, so -sigma
                (reflecting across x=0 negates a position's X while leaving
                 Y and Z alone; a rotation about X survives and Y/Z flip)
  scale, blend  multiplicative or 0..1, neutral value 1.0 — never negated

flags_cns bit 5 turns out to be exactly the rotation/not-rotation distinction:
across 19884 shipped constraints it is set for Rotation / UnkCtrl_4 /
UnkRotation_* and clear for Translation / Scale / BlendShape / Material_*,
agreeing with the transform type 99.6% of the time.  An earlier version of this
module treated it as an opaque "shipped file convention" flag; both descriptions
reproduce the same 6271 of 7451 shipped pairs, but only the vector/pseudovector
reading explains why, and it needs no unexplained bit.

Caveats worth keeping in view:
  * sigma was measured on two skeletons, both giving X:+1, Y:-1, Z:-1 for
    rotations.  Other character rigs may differ, which is why the caller passes
    measured values in rather than trusting the default.
  * roughly one shipped pair in six still does not match, mostly differing in
    magnitude rather than sign — that is deliberate asymmetric authoring and
    must not be overwritten silently.

No `bpy` import, so the rule stays testable without Blender.
"""

import re

from jcns_flags import is_angular as _is_angular_by_type

# Measured on two skeletons.  Used only when the armature is unavailable.
SIGMA_DEFAULT = {'X': +1, 'Y': -1, 'Z': -1, 'W': None}

# flags_cns bit that predicts the output sign convention.
OUTPUT_SIGN_BIT = 5


def output_sign_from_flags(flags):
    """+1 when flags_cns bit 5 is set, -1 otherwise."""
    return +1 if (int(flags) >> OUTPUT_SIGN_BIT) & 1 else -1


def sigma_from_frames(left_cols, right_cols, tol=1e-3):
    """Per-axis mirror sign for one L/R bone pair.

    `left_cols` / `right_cols` are the three local-axis direction vectors of the
    two bones in world space, as (x, y, z) triples.  Returns {'X': ±1, …} with
    None for any axis whose frames are not mirror-compatible.
    """
    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def length(v):
        return (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5

    out = {}
    for i, ax in enumerate("XYZ"):
        u, v = left_cols[i], right_cols[i]
        m = (-u[0], u[1], u[2])            # reflect across the YZ plane
        if length(sub(v, m)) < tol:
            out[ax] = -1
        elif length(add(v, m)) < tol:
            out[ax] = +1
        else:
            out[ax] = None
    out['W'] = None
    return out


# Outputs that carry no sign at all: scale factors and blend weights are
# multiplicative or 0..1, so their neutral value is 1.0 rather than 0 and
# negating them is meaningless.  Shipped files agree — L_Calf_HJ_00's Scale
# constraint carries To=[1, 1, 1.4] on BOTH sides.
UNSIGNED_OUTPUT_TYPES = frozenset({
    'Scale', 'BlendShape', 'Scalar',
    'Material_Color', 'Material_4D', 'Material_3D', 'Material_2D',
})

def is_angular_output(transform_type, flags=None):
    """Whether the driven quantity is a rotation rather than a position.

    Delegates to jcns_flags, which holds the transform-type table measured off
    the shipped files.  flags_cns bit 5 encodes exactly this distinction and is
    used only as a fallback for transform types nobody has identified yet.
    """
    return _is_angular_by_type(transform_type, flags)


def signs_for(source_axis, target_axis, flags, src_sigma=None, tgt_sigma=None,
              transform_type='Rotation'):
    """(in_sign, out_sign) for one source of one constraint.

    `src_sigma` / `tgt_sigma` are the per-axis dicts from sigma_from_frames() for
    the source and target bones; SIGMA_DEFAULT is used where they are missing.
    Returns (None, None) when either axis has no usable sign.

    sigma_from_frames() reports the PSEUDOVECTOR sign, which is what a rotation
    follows.  A position is an ordinary vector and mirrors the other way round —
    reflecting across x=0 negates a position's X component while leaving Y and Z
    alone, whereas a rotation about X survives and Y/Z flip.  The two are exact
    negatives of one another, so a translation output simply takes -sigma.

    This is what an earlier version of this module mistook for a "shipped file
    convention" governed by flags_cns bit 5.  Both descriptions score identically
    (6271 of 7451 pairs), because the bit merely tracks the transform type — but
    only this one explains *why*.
    """
    ss = (src_sigma or SIGMA_DEFAULT).get(source_axis)
    ts = (tgt_sigma or SIGMA_DEFAULT).get(target_axis)
    if ss is None or ts is None:
        return None, None
    # The source is read as a bone rotation, so the input always uses sigma.
    if transform_type in UNSIGNED_OUTPUT_TYPES:
        return ss, +1
    if is_angular_output(transform_type, flags):
        return ss, ts
    return ss, -ts


def mirror_triples(from_triple, to_triple, in_sign, out_sign):
    """Apply f_R(x) = out_sign * f_L(in_sign * x) to the stored anchors.

    Negating the input turns the MapFrom range around, so it is negated *and*
    reversed; MapTo is reversed alongside it to keep each output anchor paired
    with its own input anchor.  Only then does MapTo take its own sign.
    """
    fs, fk, fe = (float(v) for v in from_triple)
    ts, tk, te = (float(v) for v in to_triple)

    if in_sign < 0:
        fs, fk, fe = -fe, -fk, -fs
        ts, tk, te = te, tk, ts
    if out_sign < 0:
        ts, tk, te = -ts, -tk, -te

    return (fs, fk, fe), (ts, tk, te)


def mirror_source(source, source_axis, target_axis, flags,
                  src_sigma=None, tgt_sigma=None, transform_type='Rotation'):
    """Mirror one source mapping. Returns (dict_of_anchors, in_sign, out_sign).

    The signs are handed back so the UI can show what was applied and how much
    to trust it.
    """
    def g(name):
        if isinstance(source, dict):
            return float(source.get(name, 0.0) or 0.0)
        return float(getattr(source, name, 0.0))

    in_sign, out_sign = signs_for(source_axis, target_axis, flags,
                                  src_sigma, tgt_sigma, transform_type)
    if in_sign is None:
        return None, None, None

    f, t = mirror_triples(
        (g('from_start'), g('from_kink'), g('from_end')),
        (g('to_start'), g('to_kink'), g('to_end')),
        in_sign, out_sign)
    return ({'from_start': f[0], 'from_kink': f[1], 'from_end': f[2],
             'to_start':   t[0], 'to_kink':   t[1], 'to_end':   t[2]},
            in_sign, out_sign)


# ---------------------------------------------------------------------------
# Bone-name side handling
# ---------------------------------------------------------------------------

# A standalone L/R token: 'hair_base_L_a_01' but never the L inside 'Colour'.
_SIDE_TOKEN = re.compile(r'(?<![A-Za-z])([LR])(?![A-Za-z])')


def token_swap(name):
    """Swap every standalone L/R token in a name.

    Blender's flip_name only understands side markers at the start or end of a
    name, so mid-name ones like 'hair_base_L_a_01_jnt_ctrl' slip past it — that
    silently skipped 110 of 509 sided bones on a real character rig.
    """
    out, n = _SIDE_TOKEN.subn(lambda m: 'R' if m.group(1) == 'L' else 'L', name)
    return out if n else name


def counterpart(name, exists, flip_name):
    """Name of the bone on the other side, or None if it cannot be resolved.

    Name-driven on purpose.  Matching by mirrored position is tempting but wrong
    on real rigs: hair and cloth strands are named symmetrically yet modelled
    asymmetrically (partners measured 0.013–0.042 away from the true mirror),
    and coincident helper joints make nearest-bone ambiguous — position picked
    'R_DoubleEyeLidJ_LOD02' over the correct 'R_LoEyeLidJ_LOD02' at distance 0.
    """
    if not name:
        return None
    for candidate in (flip_name(name), token_swap(name)):
        if candidate and candidate != name and exists(candidate):
            return candidate
    return None


def side_from_position(x, deadzone=1e-4):
    """Which side a bone instance is on, from its position along the mirror axis.

    The better classifier: on the rig this was checked against, all 163 L-named
    bones sat at positive X and all 163 R-named ones at negative X with no
    disagreement, while 110 sided bones carried no marker flip_name could see.
    """
    if x > deadzone:
        return 'L'
    if x < -deadzone:
        return 'R'
    return None


def side_of(name, flip_name):
    """Side from the name alone — fallback when the bone is not in the armature."""
    if not name:
        return None
    other = flip_name(name)
    if other == name:
        other = token_swap(name)
    if other == name:
        return None
    for a, b in zip(name, other):
        if a != b:
            if a in 'Ll':
                return 'L'
            if a in 'Rr':
                return 'R'
            return None
    return None


def constraint_signature(target_bone, transform_type, target_axis, sources):
    """Identity used to decide whether a mirrored counterpart already exists.

    Includes the source bones because one bone axis can legitimately carry
    several constraints, and overwriting the wrong one would be silent.
    """
    return (target_bone, transform_type, target_axis,
            tuple((s[0], s[1]) for s in sources))
