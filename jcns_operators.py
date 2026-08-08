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
from bpy.props import StringProperty, BoolProperty, EnumProperty


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
                           use_radians=True, var='var'):
    """
    Build a Blender SCRIPTED driver expression implementing the three-point
    piecewise linear mapping.

    use_radians=True  → values are in degrees, converted to radians (Rotation).
    use_radians=False → values are used as-is (Location / Scale).

    `var` is the name of the Blender driver variable to read.  Multi-source
    constraints build one expression per source, each with its own variable,
    and combine the results.

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
        return f"max({lo:.6f}, min({hi:.6f}, {ts:.6f} + ({var} - {fs:.6f}) * {k1:.6f}))"

    # Only seg2 active (from_start == from_kink)
    if abs(span1) < 1e-9:
        k2 = (te - tk) / span2
        lo, hi = min(tk, te), max(tk, te)
        return f"max({lo:.6f}, min({hi:.6f}, {tk:.6f} + ({var} - {fk:.6f}) * {k2:.6f}))"

    k1 = (tk - ts) / span1
    k2 = (te - tk) / span2
    s1 = f"max({min(ts,tk):.6f}, min({max(ts,tk):.6f}, {ts:.6f} + ({var} - {fs:.6f}) * {k1:.6f}))"
    s2 = f"max({min(tk,te):.6f}, min({max(tk,te):.6f}, {tk:.6f} + ({var} - {fk:.6f}) * {k2:.6f}))"

    # Pick segment based on which side of fk the source is on.
    # The whole conditional MUST stay parenthesised: `X if c else Y` binds looser
    # than `+`, so an unwrapped ternary silently reassociates when several source
    # expressions are summed together for a multi-source constraint.
    cond = "<=" if fs <= fe else ">="
    return f"(({s1}) if {var} {cond} {fk:.6f} else ({s2}))"


_COMBINE_OPS = {
    'SUM':     lambda parts: "(" + " + ".join(parts) + ")",
    'MAX':     lambda parts: "max(" + ", ".join(parts) + ")",
    'MIN':     lambda parts: "min(" + ", ".join(parts) + ")",
    'AVERAGE': lambda parts: "((" + " + ".join(parts) + ") / %d)" % len(parts),
    'FIRST':   lambda parts: parts[0],
}

# TransformationID -> (Blender data path, driver variable prefix, values are angles)
# bt TransformationID names ID 0 "Translation"; Blender's data path is "location".
# These used to disagree ('Translation' vs 'Location'), so every Translation
# constraint silently failed to produce a driver — and Translation is the single
# most common type in shipped files (5852 of 19884 constraints).
_AXIS_NAME = ['X', 'Y', 'Z', 'W']

_DRIVABLE = {
    'Translation': ('location',        ['LOC_X', 'LOC_Y', 'LOC_Z'],     False),
    'Rotation':    ('rotation_euler',  ['ROT_X', 'ROT_Y', 'ROT_Z'],     True),
    'Scale':       ('scale',           ['SCALE_X', 'SCALE_Y', 'SCALE_Z'], False),
}


def _sources_for_driver(cns_props):
    """Convert a constraint's JCNSSourceProperties collection into driver dicts."""
    from . import AXIS_TO_INT
    out = []
    for sp in cns_props.sources:
        out.append({
            'bone':       sp.source_bone,
            'axis_idx':   AXIS_TO_INT.get(sp.source_axis, 0),
            'axis_name':  sp.source_axis,
            'from_start': sp.from_start, 'from_kink': sp.from_kink, 'from_end': sp.from_end,
            'to_start':   sp.to_start,   'to_kink':   sp.to_kink,   'to_end':   sp.to_end,
        })
    return out


def _apply_driver(armature_obj, target_bone_name, target_axis_idx,
                  sources, transform_type='Rotation', combine='SUM'):
    """
    Install (or replace) the SCRIPTED driver for one channel of one pose bone.

    `sources` is a list of dicts, one per ConstraintSource_v2:
        {bone, axis_idx, from_start, from_kink, from_end,
                         to_start,   to_kink,   to_end}

    The anchors are handed to jcns_drivers.register_channel() and the expression
    is reduced to a jcns_ch(...) call — Blender truncates expressions past 255
    characters, and a single inline mapping already costs ~170 of them.

    Sources are read in LOCAL_SPACE.  Returns (ok: bool, error_str: str).
    """
    from . import jcns_drivers

    pose_bone = armature_obj.pose.bones.get(target_bone_name)
    if pose_bone is None:
        return False, "找不到目标骨骼「%s」" % target_bone_name

    entry = _DRIVABLE.get(transform_type)
    if entry is None:
        return False, "变换类型「%s」在 Blender 中没有对应通道" % transform_type
    data_path, var_types, use_radians = entry

    usable = [s for s in sources if s.get('bone')]
    if not usable:
        return False, "未设置驱动骨骼"

    if transform_type == 'Rotation' and pose_bone.rotation_mode not in _EULER_MODES:
        pose_bone.rotation_mode = 'XYZ'

    # Anchors in the driver's own units, so the namespace function converts nothing.
    maps = []
    for s in usable:
        vals = (s['from_start'], s['from_kink'], s['from_end'],
                s['to_start'],   s['to_kink'],   s['to_end'])
        maps.append(tuple(math.radians(v) for v in vals) if use_radians else tuple(vals))

    key = jcns_drivers.channel_id(armature_obj.name, target_bone_name,
                                  transform_type, _AXIS_NAME[target_axis_idx])
    jcns_drivers.register_channel(key, maps, combine)

    pose_bone.driver_remove(data_path, target_axis_idx)
    armature_obj.animation_data_create()
    fc = armature_obj.driver_add(
        'pose.bones["%s"].%s' % (target_bone_name, data_path), target_axis_idx)

    drv = fc.driver
    drv.type = 'SCRIPTED'
    while drv.variables:
        drv.variables.remove(drv.variables[0])

    names = []
    for i, s in enumerate(usable):
        var_name = 'v%d' % i
        names.append(var_name)
        var = drv.variables.new()
        var.name = var_name
        var.type = 'TRANSFORMS'
        tgt = var.targets[0]
        tgt.id = armature_obj
        tgt.bone_target = s['bone']
        tgt.transform_type = var_types[min(s.get('axis_idx', 0), 2)]
        tgt.transform_space = 'LOCAL_SPACE'

    expr = 'jcns_ch("%s",%s)' % (key, ",".join(names))
    drv.expression = expr

    if len(expr) > 255:
        return False, ("channel key too long (%d chars) — rename the bone or "
                       "armature" % len(expr))

    print("[JCNS DRIVER] [%s] %d source(s) %s -> %s[%d]  combine=%s  expr=%s" % (
        transform_type, len(usable), ", ".join(s['bone'] for s in usable),
        target_bone_name, target_axis_idx,
        combine if len(usable) > 1 else 'n/a', expr))
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

def _apply_channel(armature_obj, root_props, members):
    """Apply one merged driver for every constraint sharing a channel.

    `members` are constraint Empties with identical (bone, transform, axis).
    Their sources are concatenated so a channel driven by several constraint
    blocks produces one driver holding all of them, rather than each apply
    silently overwriting the last.
    """
    from . import AXIS_TO_INT
    bone, transform, axis = _channel_of(members[0])

    sources = []
    for empty in members:
        sources.extend(_sources_for_driver(empty.jcns_cns_props))

    label = "%s(%s) <- %d source(s) from %d constraint(s)" % (
        bone or '???', axis, len(sources), len(members))

    if not sources:
        return False, "没有驱动源", label
    if axis == 'W' or any(s['axis_name'] == 'W' for s in sources):
        return False, "W 轴暂不支持", label
    if not bone:
        return False, "目标骨骼未解析", label

    ok, err = _apply_driver(
        armature_obj, bone, AXIS_TO_INT.get(axis, 0), sources,
        transform_type=transform, combine=root_props.source_combine,
    )
    if ok:
        for empty in members:
            empty.jcns_cns_props.driver_applied = True
    return ok, err, label


def _channel_of(empty):
    from . import channel_key
    return channel_key(empty.jcns_cns_props)


class JCNS_OT_ApplySingleDriver(Operator):
    """Apply the driver for this constraint's target channel

    Any other constraint driving the same bone axis is merged into the same
    driver, because Blender only allows one driver per channel.
    """
    bl_idname = "jcns.apply_single_driver"
    bl_label  = "应用驱动器"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint, get_jcns_root_from_constraint
        cns_obj, cns_props = get_jcns_constraint(context)
        if cns_obj is None or cns_props.constraint_type != 'Ranges':
            return False
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)
        return (root_obj is not None and root_props is not None
                and root_props.target_armature is not None)

    def execute(self, context):
        from . import (get_jcns_constraint, get_jcns_root_from_constraint,
                       group_constraints_by_channel, channel_key)
        cns_obj, cns_props = get_jcns_constraint(context)
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)

        key = channel_key(cns_props)
        members = group_constraints_by_channel(root_obj).get(key, [cns_obj])

        ok, err, label = _apply_channel(root_props.target_armature, root_props, members)
        if not ok:
            self.report({'WARNING'}, "%s — %s" % (label, err))
            return {'CANCELLED'}

        extra = ("　（该通道合并了 %d 条约束）" % len(members)
                 if len(members) > 1 else "")
        self.report({'INFO'}, "驱动器：%s%s" % (label, extra))
        return {'FINISHED'}


class JCNS_OT_ApplyAllDrivers(Operator):
    """Apply drivers for every target channel in the active JCNS collection"""
    bl_idname = "jcns.apply_all_drivers"
    bl_label  = "应用全部驱动器"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        return root_obj is not None and armature_obj is not None

    def execute(self, context):
        from . import group_constraints_by_channel

        root_obj, root_props, armature_obj = _get_root_and_armature(context)
        groups = group_constraints_by_channel(root_obj)
        if not groups:
            self.report({'WARNING'}, "该 JCNS 集合中没有约束。")
            return {'CANCELLED'}

        applied = skipped = 0
        merged_channels = 0
        for key, members in groups.items():
            if len(members) > 1:
                merged_channels += 1
            ok, err, label = _apply_channel(armature_obj, root_props, members)
            if ok:
                applied += 1
                print("[JCNS OK  ] %s" % label)
            else:
                skipped += 1
                print("[JCNS SKIP] %s - %s" % (label, err))

        n_cns = sum(len(m) for m in groups.values())
        msg = "已应用 %d 条驱动器，覆盖 %d 条约束" % (applied, n_cns)
        if merged_channels:
            msg += "；其中 %d 个通道合并了多条约束" % merged_channels
        if skipped:
            msg += "；跳过 %d 条，详见系统控制台" % skipped
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}



class JCNS_OT_ClearAllDrivers(Operator):
    """Remove all JCNS-applied rotation drivers from the target armature"""
    bl_idname = "jcns.clear_drivers"
    bl_label  = "清除全部驱动器"
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

        _DATA_PATH = {k: v[0] for k, v in _DRIVABLE.items()}

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

        self.report({'INFO'}, f"已清除 {removed} 条驱动器。")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Add Constraint
# ---------------------------------------------------------------------------

class JCNS_OT_AddConstraint(Operator):
    """Add a new blank constraint Empty to the active JCNS collection"""
    bl_idname = "jcns.add_constraint"
    bl_label  = "新增约束"
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
            self.report({'ERROR'}, "根节点不属于任何集合。")
            return {'CANCELLED'}

        name = make_constraint_empty_name(new_idx, 'BoneName', '', 'X')
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = 'ARROWS'
        obj.empty_display_size = 0.05
        obj.parent = root_obj
        coll.objects.link(obj)

        p = obj.jcns_cns_props
        p.is_jcns_constraint = True
        p.constraint_type = 'Ranges'
        p.target_bone = ''
        p.transform_type = 'Rotation'
        sp = p.sources.add()          # every new constraint starts with one source
        sp.source_bone = 'BoneName'


        context.view_layer.objects.active = obj
        obj.select_set(True)
        self.report({'INFO'}, f"已新增约束「{name}」。")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Delete Constraint
# ---------------------------------------------------------------------------

class JCNS_OT_DeleteConstraint(Operator):
    """Remove the selected constraint Empty and reindex remaining Empties"""
    bl_idname = "jcns.delete_constraint"
    bl_label  = "删除约束"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, _ = get_jcns_constraint(context)
        return obj is not None

    def execute(self, context):
        from . import (get_jcns_constraint, get_jcns_root_from_constraint,
                       get_constraint_empties, constraint_name_from_props)

        cns_obj, cns_props = get_jcns_constraint(context)
        root_obj, root_props = get_jcns_root_from_constraint(cns_obj)

        # Remove the selected Empty
        bpy.data.objects.remove(cns_obj, do_unlink=True)

        # Reindex remaining Empties
        if root_obj:
            remaining = get_constraint_empties(root_obj)
            for new_idx, empty in enumerate(remaining):
                empty.name = constraint_name_from_props(new_idx, empty.jcns_cns_props)

        self.report({'INFO'}, "约束已删除，其余已重新编号。")
        return {'FINISHED'}



class JCNS_OT_AddSource(Operator):
    """Add another driving source to the selected constraint"""
    bl_idname = "jcns.add_source"
    bl_label  = "新增驱动源"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, props = get_jcns_constraint(context)
        return obj is not None and props.constraint_type == 'Ranges'

    def execute(self, context):
        from . import get_jcns_constraint, constraint_name_from_props
        cns_obj, p = get_jcns_constraint(context)
        sp = p.sources.add()
        # Seed from the previous source so the new one is a usable starting point
        if len(p.sources) > 1:
            prev = p.sources[len(p.sources) - 2]
            for attr in ('source_axis', 'from_start', 'from_kink', 'from_end',
                         'to_start', 'to_kink', 'to_end', 'update_timing',
                         'src_transform_id', 'rest_quat_w'):
                setattr(sp, attr, getattr(prev, attr))
        p.active_source_index = len(p.sources) - 1
        idx = 0
        if cns_obj.name.startswith('['):
            try:
                idx = int(cns_obj.name[1:cns_obj.name.index(']')])
            except (ValueError, IndexError):
                pass
        cns_obj.name = constraint_name_from_props(idx, p)
        self.report({'INFO'}, "已新增第 %d 个驱动源。" % len(p.sources))
        return {'FINISHED'}


class JCNS_OT_RemoveSource(Operator):
    """Remove the selected driving source from this constraint"""
    bl_idname = "jcns.remove_source"
    bl_label  = "删除驱动源"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, props = get_jcns_constraint(context)
        return obj is not None and len(props.sources) > 1

    def execute(self, context):
        from . import get_jcns_constraint, constraint_name_from_props
        cns_obj, p = get_jcns_constraint(context)
        i = min(p.active_source_index, len(p.sources) - 1)
        p.sources.remove(i)
        p.active_source_index = max(0, min(i, len(p.sources) - 1))
        idx = 0
        if cns_obj.name.startswith('['):
            try:
                idx = int(cns_obj.name[1:cns_obj.name.index(']')])
            except (ValueError, IndexError):
                pass
        cns_obj.name = constraint_name_from_props(idx, p)
        self.report({'INFO'}, "已删除驱动源，剩余 %d 个。" % len(p.sources))
        return {'FINISHED'}



class JCNS_OT_SwapMapToEnds(Operator):
    """Exchange MapTo start and end on the active source

    Fixes the usual authoring slip: when MapFrom runs downwards the rest pose
    lands on anchor C, so a deflection written into to_end is what the bone
    holds while idle instead of what it reaches when the driver bone moves.
    """
    bl_idname = "jcns.swap_mapto_ends"
    bl_label  = "对调输出首尾"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, props = get_jcns_constraint(context)
        return (obj is not None and props.constraint_type == 'Ranges'
                and len(props.sources) > 0)

    def execute(self, context):
        from . import get_jcns_constraint
        from .modules_shim import get_mapping
        _, p = get_jcns_constraint(context)
        sp = p.sources[min(p.active_source_index, len(p.sources) - 1)]
        before = get_mapping().describe(sp)['at_rest']
        sp.to_start, sp.to_end = sp.to_end, sp.to_start
        after = get_mapping().describe(sp)['at_rest']
        self.report({'INFO'},
                    "输出首尾已对调 —— 静止输出 %+.2f° → %+.2f°" % (before, after))
        return {'FINISHED'}



class JCNS_OT_MirrorConstraints(Operator):
    """把选中的约束镜像到骨架的另一侧

    符号由骨骼的局部坐标系决定，并区分驱动量的类型：旋转是赝矢量、位移是普通
    矢量，两者镜像方式相反；缩放与形变权重不带符号，永不取反。
    """
    bl_idname = "jcns.mirror_constraints"
    bl_label  = "镜像到另一侧"
    bl_options = {'REGISTER', 'UNDO'}

    overwrite: BoolProperty(
        name="覆盖已有数值", default=False,
        description="对侧若已存在同名约束，是否用镜像结果覆盖它的数值。"
                    "官方文件里约十分之一的左右配对是有意做成不对称的，"
                    "所以默认不覆盖")
    use_frames: BoolProperty(
        name="从骨架读取符号", default=True,
        description="实测每对骨骼的局部坐标系来决定符号。关闭则使用在官方"
                    "骨架上量到的默认值（X:+1, Y:-1, Z:-1）")

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, props = get_jcns_constraint(context)
        return obj is not None and props.constraint_type == 'Ranges'

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "use_frames")
        layout.prop(self, "overwrite")
        col = layout.column(align=True)
        col.label(text="符号由骨骼坐标系与驱动量类型决定", icon='INFO')
        col.label(text="旋转与位移的镜像方式相反，已自动区分")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from . import (get_jcns_constraint, get_jcns_root_from_constraint,
                       get_constraint_empties, constraint_name_from_props)
        from .modules_shim import get_mirror

        active, _ = get_jcns_constraint(context)
        root_obj, root_props = get_jcns_root_from_constraint(active)
        if root_obj is None:
            self.report({'ERROR'}, "找不到所属的 JCNS 根节点。")
            return {'CANCELLED'}

        # Work on the selection so several can be done at once, but the active
        # constraint is always included — that is what the panel button implies.
        targets = [o for o in context.selected_objects
                   if getattr(o, 'jcns_cns_props', None)
                   and o.jcns_cns_props.is_jcns_constraint
                   and o.jcns_cns_props.constraint_type == 'Ranges']
        if active not in targets:
            targets.append(active)

        mirror = get_mirror()
        flip = bpy.utils.flip_name
        arm = root_props.target_armature if root_props else None
        sigma_cache = {}

        def bone_exists(name):
            return arm is None or name in arm.data.bones

        def partner(name):
            return mirror.counterpart(name, bone_exists, flip)

        def sigma(name):
            if not self.use_frames or arm is None:
                return None
            if name in sigma_cache:
                return sigma_cache[name]
            b, mate = arm.data.bones.get(name), partner(name)
            v = None
            if b is not None and mate:
                r = arm.data.bones.get(mate)
                if r is not None:
                    L, R = b.matrix_local.to_3x3(), r.matrix_local.to_3x3()
                    v = mirror.sigma_from_frames(
                        [tuple(L.col[i]) for i in range(3)],
                        [tuple(R.col[i]) for i in range(3)])
            sigma_cache[name] = v
            return v

        existing = {}
        for e in get_constraint_empties(root_obj):
            p = e.jcns_cns_props
            existing.setdefault(mirror.constraint_signature(
                p.target_bone, p.transform_type, p.target_axis,
                [(s.source_bone, s.source_axis) for s in p.sources]), e)

        coll = next(iter(root_obj.users_collection), None)
        made = updated = kept = 0
        problems = []

        for e in targets:
            p = e.jcns_cns_props
            new_tgt = partner(p.target_bone)
            if new_tgt is None:
                problems.append("%s 没有对侧骨骼" % (p.target_bone or "?"))
                continue

            tgt_sigma = sigma(p.target_bone)
            new_sources, failed = [], None
            for sp in p.sources:
                mate = partner(sp.source_bone)
                if mate is None:
                    failed = "%s 没有对侧骨骼" % sp.source_bone
                    break
                vals, i_s, o_s = mirror.mirror_source(
                    sp, sp.source_axis, p.target_axis, p.cns_flags,
                    sigma(sp.source_bone), tgt_sigma, p.transform_type)
                if vals is None:
                    failed = "%s 的 %s 轴无法确定镜像符号" % (sp.source_bone,
                                                             sp.source_axis)
                    break
                new_sources.append((mate, sp.source_axis, vals, sp))
            if failed:
                problems.append(failed)
                continue

            sig = mirror.constraint_signature(
                new_tgt, p.transform_type, p.target_axis,
                [(s[0], s[1]) for s in new_sources])

            dst = existing.get(sig)
            if dst is not None and not self.overwrite:
                kept += 1
                continue
            if dst is None:
                dst = bpy.data.objects.new("jcns_mirrored", None)
                dst.empty_display_type = 'ARROWS'
                dst.empty_display_size = 0.05
                dst.parent = root_obj
                coll.objects.link(dst)
                existing[sig] = dst
                made += 1
            else:
                updated += 1

            q = dst.jcns_cns_props
            q.is_jcns_constraint = True
            q.constraint_type = 'Ranges'
            q.target_bone = new_tgt
            q.target_axis = p.target_axis
            q.transform_type = p.transform_type
            q.cns_flags = p.cns_flags
            q.sources.clear()
            for bone, axis, vals, orig in new_sources:
                ns = q.sources.add()
                ns.source_bone = bone
                ns.source_axis = axis
                for k, v in vals.items():
                    setattr(ns, k, v)
                for attr in ('rest_quat_x', 'rest_quat_y', 'rest_quat_z',
                             'rest_quat_w', 'update_timing', 'src_transform_id',
                             'unk_byte2', 'unknown_uint16', 'unknown_uint32_2'):
                    setattr(ns, attr, getattr(orig, attr))

        for i, empty in enumerate(get_constraint_empties(root_obj)):
            empty.name = constraint_name_from_props(i, empty.jcns_cns_props)

        parts = []
        if made:
            parts.append("新建 %d 条" % made)
        if updated:
            parts.append("更新 %d 条" % updated)
        if kept:
            parts.append("跳过 %d 条已存在的（可勾选覆盖）" % kept)
        msg = "镜像完成：" + ("，".join(parts) if parts else "无改动")
        if problems:
            msg += "；" + "，".join(problems[:2])
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}



class JCNS_OT_SortAnchors(Operator):
    """把三个锚点按源角度重新排序

    锚点折返时（例如 [-120, 0, -30]），映射按「输入在折点哪一侧」选择线段，
    于是有一个锚点永远取不到 —— 改它不会有任何效果。排序会把每个输出和它自己
    的输入一起搬动，曲线形状因此保持不变，只是所有锚点重新可用。
    """
    bl_idname = "jcns.sort_anchors"
    bl_label  = "按源角度排序锚点"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        obj, props = get_jcns_constraint(context)
        return (obj is not None and props.constraint_type == 'Ranges'
                and len(props.sources) > 0)

    def execute(self, context):
        from . import get_jcns_constraint
        from .modules_shim import get_mapping
        _, p = get_jcns_constraint(context)
        sp = p.sources[min(p.active_source_index, len(p.sources) - 1)]

        pairs = sorted(((sp.from_start, sp.to_start),
                        (sp.from_kink,  sp.to_kink),
                        (sp.from_end,   sp.to_end)), key=lambda t: t[0])
        # keep the file's own direction: a descending MapFrom stays descending
        if sp.from_start > sp.from_end:
            pairs.reverse()
        (sp.from_start, sp.to_start), (sp.from_kink, sp.to_kink),             (sp.from_end, sp.to_end) = pairs

        d = get_mapping().plain_description(sp)
        self.report({'INFO'}, "锚点已排序：%s" % (
            "全部可用" if d['unreachable_anchor'] is None
            else "仍有锚点 %s 取不到" % d['unreachable_anchor']))
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
    JCNS_OT_AddSource,
    JCNS_OT_RemoveSource,
    JCNS_OT_SwapMapToEnds,
    JCNS_OT_SortAnchors,
    JCNS_OT_MirrorConstraints,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
