"""
jcns_operators.py
-----------------
Drive operators and constraint management operators.

Operators:
  JCNS_OT_ApplySingleDriver  – apply driver for the selected constraint Empty
  JCNS_OT_ApplyAllDrivers    – apply drivers for all constraint Empties in the collection
  JCNS_OT_ClearAllDrivers    – remove all JCNS drivers from the target armature
  JCNS_OT_AddConstraint      – add a new empty constraint to the active JCNS collection
  JCNS_OT_DeleteConstraint   – remove the selected constraint Empty and reindex names
"""

import os
import sys
import math
import bpy
from bpy.types import Operator
from bpy.props import StringProperty


# ---------------------------------------------------------------------------
# Module path helper
# ---------------------------------------------------------------------------

def _ensure_modules_path():
    addon_dir = os.path.dirname(__file__)
    modules_dir = os.path.join(addon_dir, "modules")
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)


# ---------------------------------------------------------------------------
# Driver math
# ---------------------------------------------------------------------------

_EULER_MODES = ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX')
_ROT_TYPE   = ['ROT_X',   'ROT_Y',   'ROT_Z']
_LOC_TYPE   = ['LOC_X',   'LOC_Y',   'LOC_Z']
_SCALE_TYPE = ['SCALE_X', 'SCALE_Y', 'SCALE_Z']


def _build_piecewise_expr(from_start, from_kink, from_end,
                           to_start,   to_kink,   to_end,
                           use_radians=True):
    """
    Build a Blender SCRIPTED driver expression implementing the three-point
    piecewise linear mapping.

    use_radians=True  → values are in degrees, converted to radians (Rotation).
    use_radians=False → values are used as-is (Location / Scale).

      Seg 1: source [from_start → from_kink]  →  output [to_start → to_kink]
      Seg 2: source [from_kink  → from_end]   →  output [to_kink  → to_end]

    The expression is clamped so output stays within the output range.
    Degenerate cases (collapsed segments) are handled gracefully.
    """
    if use_radians:
        fs = math.radians(from_start)
        fk = math.radians(from_kink)
        fe = math.radians(from_end)
        ts = math.radians(to_start)
        tk = math.radians(to_kink)
        te = math.radians(to_end)
    else:
        fs, fk, fe = from_start, from_kink, from_end
        ts, tk, te = to_start,   to_kink,   to_end

    span1 = fk - fs
    span2 = fe - fk

    # Both segments degenerate → constant output
    if abs(span1) < 1e-9 and abs(span2) < 1e-9:
        return f"{ts:.6f}"

    # Only seg1 active (from_kink == from_end)
    if abs(span2) < 1e-9:
        k1 = (tk - ts) / span1
        lo, hi = min(ts, tk), max(ts, tk)
        return f"max({lo:.6f}, min({hi:.6f}, {ts:.6f} + (var - {fs:.6f}) * {k1:.6f}))"

    # Only seg2 active (from_start == from_kink)
    if abs(span1) < 1e-9:
        k2 = (te - tk) / span2
        lo, hi = min(tk, te), max(tk, te)
        return f"max({lo:.6f}, min({hi:.6f}, {tk:.6f} + (var - {fk:.6f}) * {k2:.6f}))"

    k1 = (tk - ts) / span1
    k2 = (te - tk) / span2
    s1 = f"max({min(ts,tk):.6f}, min({max(ts,tk):.6f}, {ts:.6f} + (var - {fs:.6f}) * {k1:.6f}))"
    s2 = f"max({min(tk,te):.6f}, min({max(tk,te):.6f}, {tk:.6f} + (var - {fk:.6f}) * {k2:.6f}))"

    # Pick segment based on which side of fk the source is on
    cond = "<=" if fs <= fe else ">="
    return f"({s1}) if var {cond} {fk:.6f} else ({s2})"


def _apply_driver(armature_obj, target_bone_name, target_axis_idx,
                  source_bone_name, source_axis_idx,
                  from_start, from_kink, from_end,
                  to_start, to_kink, to_end,
                  transform_type='Rotation'):
    """
    Install (or replace) a SCRIPTED piecewise-linear driver for Rotation,
    Location, or Scale.

    Three anchor points define a two-segment linear mapping:
      A  (from_start, to_start)  — first endpoint
      B  (from_kink,  to_kink)   — slope-change / fold point
      C  (from_end,   to_end)    — second endpoint

    Rotation: values in degrees → converted to radians; drives rotation_euler.
              Euler mode forced if bone is in QUATERNION/AXIS_ANGLE.
    Location: values used as-is (Blender units); drives location.
    Scale:    values used as-is (multipliers); drives scale.

    Source read in LOCAL_SPACE. Returns (ok: bool, error_str: str).
    """
    pose_bone = armature_obj.pose.bones.get(target_bone_name)
    if pose_bone is None:
        return False, f"Target bone '{target_bone_name}' not found"

    ax = min(source_axis_idx, 2)

    if transform_type == 'Rotation':
        if pose_bone.rotation_mode not in _EULER_MODES:
            pose_bone.rotation_mode = 'XYZ'
        data_path   = "rotation_euler"
        var_tf_type = _ROT_TYPE[ax]
        use_radians = True
    elif transform_type == 'Location':
        data_path   = "location"
        var_tf_type = _LOC_TYPE[ax]
        use_radians = False
    elif transform_type == 'Scale':
        data_path   = "scale"
        var_tf_type = _SCALE_TYPE[ax]
        use_radians = False
    else:
        return False, f"Transform type '{transform_type}' is not supported for drivers"

    pose_bone.driver_remove(data_path, target_axis_idx)

    armature_obj.animation_data_create()
    pose_path = f'pose.bones["{target_bone_name}"].{data_path}'
    fc = armature_obj.driver_add(pose_path, target_axis_idx)

    expr = _build_piecewise_expr(from_start, from_kink, from_end,
                                  to_start,   to_kink,   to_end,
                                  use_radians=use_radians)

    drv = fc.driver
    drv.type = 'SCRIPTED'
    drv.expression = expr

    while drv.variables:
        drv.variables.remove(drv.variables[0])

    var = drv.variables.new()
    var.name = 'var'
    var.type = 'TRANSFORMS'
    tgt = var.targets[0]
    tgt.id = armature_obj
    tgt.bone_target = source_bone_name
    tgt.transform_type = var_tf_type
    tgt.transform_space = 'LOCAL_SPACE'

    print(f"[JCNS DRIVER] [{transform_type}] {source_bone_name}({source_axis_idx}) → "
          f"{target_bone_name}[{target_axis_idx}]  expr={expr}")

    return True, ""


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _get_root_and_armature(context):
    """
    From the active object (root or constraint Empty), return:
      (root_empty, root_props, armature_obj)  or  (None, None, None) on failure.
    """
    from . import get_jcns_root, get_jcns_constraint, get_jcns_root_from_constraint

    obj, root_props = get_jcns_root(context)
    if obj is None:
        # Maybe a constraint Empty is active — walk up to root
        cns_obj, _ = get_jcns_constraint(context)
        if cns_obj:
            obj, root_props = get_jcns_root_from_constraint(cns_obj)

    if obj is None or root_props is None:
        return None, None, None

    armature_obj = root_props.target_armature
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return obj, root_props, None

    return obj, root_props, armature_obj


def _get_active_constraint_props(context):
    """Return jcns_cns_props of active object if it is a constraint Empty, else None."""
    from . import get_jcns_constraint
    _, props = get_jcns_constraint(context)
    return props


# ---------------------------------------------------------------------------
# Operator: Apply Single Driver
# ---------------------------------------------------------------------------

class JCNS_OT_ApplySingleDriver(Operator):
    """Apply the selected constraint Empty as a rotation driver on the target armature"""
    bl_idname = "jcns.apply_single_driver"
    bl_label  = "Apply Driver"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint, get_jcns_root_from_constraint
        cns_obj, cns_props = get_jcns_constraint(context)
        if cns_obj is None:
            return False
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)
        return (root_obj is not None and
                root_props is not None and
                root_props.target_armature is not None)

    def execute(self, context):
        from . import (AXIS_TO_INT, get_jcns_constraint,
                       get_jcns_root_from_constraint)
        cns_obj, cns_props = get_jcns_constraint(context)
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)
        armature_obj = root_props.target_armature

        src_ax = AXIS_TO_INT.get(cns_props.source_axis, 0)
        tgt_ax = AXIS_TO_INT.get(cns_props.target_axis, 0)

        if cns_props.source_axis == 'W' or cns_props.target_axis == 'W':
            self.report({'WARNING'},
                        "W-axis (quaternion W) constraints are not yet supported as drivers.")
            return {'CANCELLED'}

        ok, err = _apply_driver(
            armature_obj,
            cns_props.target_bone, tgt_ax,
            cns_props.source_bone, src_ax,
            cns_props.from_start, cns_props.from_kink, cns_props.from_end,
            cns_props.to_start, cns_props.to_kink, cns_props.to_end,
            transform_type=cns_props.transform_type,
        )
        if not ok:
            self.report({'WARNING'}, err)
            return {'CANCELLED'}

        cns_props.driver_applied = True
        self.report(
            {'INFO'},
            f"[{cns_props.transform_type}] Driver: '{cns_props.source_bone}'({cns_props.source_axis}) "
            f"→ '{cns_props.target_bone}'({cns_props.target_axis})"
        )
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Apply All Drivers
# ---------------------------------------------------------------------------

class JCNS_OT_ApplyAllDrivers(Operator):
    """Apply drivers for all constraint Empties in the active JCNS collection"""
    bl_idname = "jcns.apply_all_drivers"
    bl_label  = "Apply All Drivers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        return root_obj is not None and armature_obj is not None

    def execute(self, context):
        from . import AXIS_TO_INT, get_constraint_empties

        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        empties = get_constraint_empties(root_obj)

        if not empties:
            self.report({'WARNING'}, "No constraint Empties found in the JCNS collection.")
            return {'CANCELLED'}

        applied = 0
        skipped = []

        for empty in empties:
            p = empty.jcns_cns_props
            if not p.source_bone:
                continue

            src_ax = AXIS_TO_INT.get(p.source_axis, 0)
            tgt_ax = AXIS_TO_INT.get(p.target_axis, 0)

            log_prefix = (
                f"[{empty.name}] "
                f"{p.source_bone}({p.source_axis}) → "
                f"{p.target_bone or '???'}({p.target_axis}) "
                f"From=[{p.from_start:.0f}, thr={p.from_kink:.0f}, {p.from_end:.0f}] "
                f"To=[{p.to_start:.0f}, thr={p.to_kink:.0f}, {p.to_end:.0f}]"
            )

            if p.source_axis == 'W' or p.target_axis == 'W':
                msg = f"SKIP: W-axis unsupported"
                skipped.append(empty.name)
                print(f"[JCNS SKIP] {log_prefix} — {msg}")
                continue

            if not p.target_bone:
                msg = "SKIP: target_bone not resolved"
                skipped.append(empty.name)
                print(f"[JCNS SKIP] {log_prefix} — {msg}")
                continue

            ok, err = _apply_driver(
                armature_obj,
                p.target_bone, tgt_ax,
                p.source_bone, src_ax,
                p.from_start, p.from_kink, p.from_end,
                p.to_start, p.to_kink, p.to_end,
                transform_type=p.transform_type,
            )
            if ok:
                applied += 1
                p.driver_applied = True
                print(f"[JCNS OK  ] {log_prefix}")
            else:
                skipped.append(empty.name)
                print(f"[JCNS FAIL] {log_prefix} — ERR: {err}")

        msg = f"Applied {applied} driver(s)"
        if skipped:
            msg += f", skipped {len(skipped)} — see System Console"
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg + " — see System Console for details")

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Clear All Drivers
# ---------------------------------------------------------------------------

class JCNS_OT_ClearAllDrivers(Operator):
    """Remove all JCNS-applied rotation drivers from the target armature"""
    bl_idname = "jcns.clear_drivers"
    bl_label  = "Clear All Drivers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        return root_obj is not None and armature_obj is not None

    def execute(self, context):
        from . import AXIS_TO_INT, get_constraint_empties

        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        empties = get_constraint_empties(root_obj)
        removed = 0

        _DATA_PATH = {
            'Rotation': 'rotation_euler',
            'Location': 'location',
            'Scale':    'scale',
        }

        for empty in empties:
            p = empty.jcns_cns_props
            if not p.target_bone:
                continue
            data_path = _DATA_PATH.get(p.transform_type)
            if data_path is None:
                continue
            tgt_ax = AXIS_TO_INT.get(p.target_axis, 0)
            pose_bone = armature_obj.pose.bones.get(p.target_bone)
            if pose_bone:
                try:
                    pose_bone.driver_remove(data_path, tgt_ax)
                    removed += 1
                    p.driver_applied = False
                except Exception:
                    pass

        self.report({'INFO'}, f"Cleared {removed} driver(s).")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Add Constraint
# ---------------------------------------------------------------------------

class JCNS_OT_AddConstraint(Operator):
    """Add a new blank constraint Empty to the active JCNS collection"""
    bl_idname = "jcns.add_constraint"
    bl_label  = "Add Constraint"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_root
        obj, _ = get_jcns_root(context)
        return obj is not None

    def execute(self, context):
        from . import (get_jcns_root, get_constraint_empties,
                       make_constraint_empty_name)

        root_obj, root_props = get_jcns_root(context)
        existing = get_constraint_empties(root_obj)
        new_idx = len(existing)

        coll = None
        for c in root_obj.users_collection:
            coll = c
            break
        if coll is None:
            self.report({'ERROR'}, "Root Empty has no collection.")
            return {'CANCELLED'}

        name = make_constraint_empty_name(new_idx, 'BoneName', '', 'X')
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = 'ARROWS'
        obj.empty_display_size = 0.05
        coll.objects.link(obj)

        # Set minimal defaults so it's recognised as a constraint Empty
        obj.jcns_cns_props.source_bone = 'BoneName'
        obj.jcns_cns_props.target_bone = ''
        obj.jcns_cns_props.transform_type = 'Rotation'

        context.view_layer.objects.active = obj
        obj.select_set(True)
        self.report({'INFO'}, f"Added constraint Empty '{name}'.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Delete Constraint
# ---------------------------------------------------------------------------

class JCNS_OT_DeleteConstraint(Operator):
    """Remove the selected constraint Empty and reindex remaining Empties"""
    bl_idname = "jcns.delete_constraint"
    bl_label  = "Delete Constraint"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, _ = get_jcns_constraint(context)
        return obj is not None

    def execute(self, context):
        from . import (get_jcns_constraint, get_jcns_root_from_constraint,
                       get_constraint_empties, make_constraint_empty_name)

        cns_obj, cns_props = get_jcns_constraint(context)
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)

        # Remove the selected Empty
        bpy.data.objects.remove(cns_obj, do_unlink=True)

        # Reindex remaining Empties
        if root_obj:
            remaining = get_constraint_empties(root_obj)
            for new_idx, empty in enumerate(remaining):
                p = empty.jcns_cns_props
                new_name = make_constraint_empty_name(
                    new_idx, p.source_bone, p.target_bone, p.target_axis, p.source_axis
                )
                empty.name = new_name

        self.report({'INFO'}, "Constraint deleted and collection reindexed.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [
    JCNS_OT_ApplySingleDriver,
    JCNS_OT_ApplyAllDrivers,
    JCNS_OT_ClearAllDrivers,
    JCNS_OT_AddConstraint,
    JCNS_OT_DeleteConstraint,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
