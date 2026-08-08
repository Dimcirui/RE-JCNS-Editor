"""
jcns_flags.py
-------------
What the bits of ConstraintInfo's `flags_cns` mean, and which of them follow
from the transform type.

Derived from 19884 constraints in 884 shipped v102 files.  Two bits turn out to
be a pure function of TransformationID — every one of the 15 observed types uses
a single value, with no exceptions at all:

    bit 4   the constraint drives a BONE rather than a material / blend weight
            set for Translation, Rotation, Scale, UnkCtrl_4, UnkTopBank_5,
            Unknown_6, UnkRotation_13/14; clear for Material_*, BlendShape,
            Scalar, Unknown_12.
            (the bt template guesses "isJoint?" — this confirms it)

    bit 5   the driven quantity is angular
            set for Rotation, UnkCtrl_4, UnkTopBank_5, Unknown_6,
            UnkRotation_13/14; clear for Translation, Scale, BlendShape,
            Material_*, Scalar, Unknown_12.
            (unnamed in the bt template)

Because they are redundant with the transform type, the exporter recomputes them
instead of trusting whatever is sitting in the raw field — otherwise changing a
constraint's transform type in the UI would leave the flags describing the old
one, and nothing would complain.

bit 0 is NOT derivable: it varies within a single type (Rotation appears as both
49 and 48), so it is a genuine per-constraint setting and is left alone.  The bt
template guesses "isAdd?".

No `bpy` import, so this stays testable without Blender.
"""

JOINT_BIT = 4
ANGULAR_BIT = 5

# transform type -> (drives a bone, quantity is angular)
# Unanimous across every shipped constraint of that type.
TRANSFORM_FLAGS = {
    'Translation':    (True,  False),
    'Rotation':       (True,  True),
    'Scale':          (True,  False),
    'BlendShape':     (False, False),
    'UnkCtrl_4':      (True,  True),
    'UnkTopBank_5':   (True,  True),
    'Unknown_6':      (True,  True),
    'Material_Color': (False, False),
    'Material_4D':    (False, False),
    'Material_3D':    (False, False),
    'Material_2D':    (False, False),
    'Scalar':         (False, False),
    'Unknown_12':     (False, False),
    'UnkRotation_13': (True,  True),
    'UnkRotation_14': (True,  True),
    # Not seen in any shipped file; assumed to follow their siblings.
    'UnkRotation_15': (True,  True),
    'UnkRotation_16': (True,  True),
}


def expected_bits(transform_type):
    """(is_joint, is_angular) for a transform type, or None if unknown."""
    return TRANSFORM_FLAGS.get(transform_type)


def is_angular(transform_type, flags=None):
    """Whether the driven quantity is a rotation rather than a position.

    Prefers the transform type, which is what the user actually edits.  Falls
    back to the file's own bit for a type nobody has identified yet.
    """
    known = TRANSFORM_FLAGS.get(transform_type)
    if known is not None:
        return known[1]
    if flags is not None:
        return bool((int(flags) >> ANGULAR_BIT) & 1)
    return False


def apply_derived_bits(flags, transform_type):
    """Return `flags` with bits 4 and 5 rewritten to match the transform type.

    Every other bit is preserved, so per-constraint settings such as bit 0
    survive untouched.  An unrecognised transform type leaves the flags alone
    rather than guessing.
    """
    known = TRANSFORM_FLAGS.get(transform_type)
    if known is None:
        return int(flags) & 0xFF
    joint, angular = known
    out = int(flags) & 0xFF
    for bit, on in ((JOINT_BIT, joint), (ANGULAR_BIT, angular)):
        out = (out | (1 << bit)) if on else (out & ~(1 << bit))
    return out & 0xFF


def describe_bits(flags):
    """Human-readable breakdown, for tooltips and diagnostics."""
    f = int(flags) & 0xFF
    return {
        'raw': f,
        'binary': format(f, '08b'),
        'drives_joint': bool(f >> JOINT_BIT & 1),
        'angular': bool(f >> ANGULAR_BIT & 1),
        'bit0': bool(f & 1),
    }
