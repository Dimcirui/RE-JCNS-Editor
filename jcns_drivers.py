"""
jcns_drivers.py
---------------
Driver-namespace function backing the generated drivers.

Blender hard-caps `Driver.expression` at 255 characters: assigning anything
longer silently truncates it, which produces an unbalanced-parenthesis
SyntaxError the moment the driver evaluates.  A single three-point mapping
already costs ~170 characters inline, so two sources on one channel overflow —
and shipped files contain channels with up to eight.

So the expression no longer carries the maths.  It reduces to a call:

    jcns_ch("Armature|L_Dress_HJ_01|Rotation|Z", var, var_001)

which stays well under the cap for any number of sources.  The anchors live in
_CHANNELS, keyed by channel, and the actual dependency on the source bones is
still declared through real driver variables so the depsgraph updates correctly.

Values in _CHANNELS are pre-converted to the driver's own units (radians for
rotation), so the function does no unit conversion.
"""

import bpy

from .modules_shim import get_mapping


# key -> {'maps': [(fs, fk, fe, ts, tk, te), …], 'combine': str}
_CHANNELS = {}


def channel_id(armature_name, bone, transform, axis):
    """Stable, human-readable key. Quotes are stripped so it is safe to embed."""
    parts = [str(p).replace('"', '').replace('\\', '') for p in
             (armature_name, bone, transform, axis)]
    return "|".join(parts)


def register_channel(key, maps, combine):
    _CHANNELS[key] = {'maps': list(maps), 'combine': combine}


def clear_channels():
    _CHANNELS.clear()


def _combine(values, mode):
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


def jcns_ch(key, *values):
    """Evaluate one driven channel. Called from every generated driver."""
    ch = _CHANNELS.get(key)
    if ch is None:
        # Cache miss — the .blend was reloaded without the channels being rebuilt.
        # Deliberately do NOT touch bpy.data here: this runs during depsgraph
        # evaluation.  rebuild_all() is called from a load_post handler instead.
        return 0.0
    ev = get_mapping().eval_piecewise
    maps = ch['maps']
    outs = [ev(*maps[i], v) for i, v in enumerate(values) if i < len(maps)]
    return _combine(outs, ch['combine'])


def rebuild_all():
    """Recreate the channel table from the scene, without touching the drivers.

    Runs after a .blend load so drivers saved in the file keep working without
    the user having to press Apply again.
    """
    import math
    from . import (AXIS_TO_INT, group_constraints_by_channel)

    clear_channels()
    rebuilt = 0
    for obj in bpy.data.objects:
        rp = getattr(obj, 'jcns_root_props', None)
        if not rp or not rp.source_filepath or rp.target_armature is None:
            continue
        arm = rp.target_armature
        for (bone, transform, axis), members in group_constraints_by_channel(obj).items():
            use_rad = (transform == 'Rotation')
            maps = []
            for empty in members:
                for sp in empty.jcns_cns_props.sources:
                    if not sp.source_bone:
                        continue
                    vals = (sp.from_start, sp.from_kink, sp.from_end,
                            sp.to_start, sp.to_kink, sp.to_end)
                    maps.append(tuple(math.radians(v) for v in vals) if use_rad else vals)
            if maps:
                register_channel(channel_id(arm.name, bone, transform, axis),
                                 maps, rp.source_combine)
                rebuilt += 1
    return rebuilt


@bpy.app.handlers.persistent
def _on_load(_dummy):
    try:
        n = rebuild_all()
        if n:
            print("[JCNS] rebuilt %d driver channel(s) after file load" % n)
    except Exception as exc:                                  # never break loading
        print("[JCNS] channel rebuild failed: %r" % exc)


def register():
    bpy.app.driver_namespace['jcns_ch'] = jcns_ch
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.app.driver_namespace.pop('jcns_ch', None)
    clear_channels()
