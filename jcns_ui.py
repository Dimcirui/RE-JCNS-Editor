"""
jcns_ui.py
----------
侧边栏面板（View3D > 侧栏 > JCNS 编辑器）。

面板按「你多久看一次」分层：

  JCNS_PT_Status                始终显示 —— 当前选中了什么
  JCNS_PT_Root                  根节点 —— 文件、骨架、驱动器按钮、导出
    JCNS_PT_RootChannels        按驱动的骨骼通道分组列出约束
  JCNS_PT_Constraint            约束 —— 目标、驱动源、映射、诊断
    JCNS_PT_ConstraintAdvanced  静止四元数与原始字节（默认折叠）

几乎每个文件都一样的字段（静止四元数、未逆向的原始字节）放进折叠的「高级」
子面板，免得和真正要调的数值抢注意力。
"""

import os
import sys

import bpy
from bpy.types import Panel


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _mapping():
    """modules/jcns_mapping.py —— 和驱动器跑同一套数学。"""
    modules_dir = os.path.join(os.path.dirname(__file__), "modules")
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)
    import jcns_mapping
    return jcns_mapping


def _curve():
    """modules/jcns_curve.py —— 折线图光栅化。"""
    modules_dir = os.path.join(os.path.dirname(__file__), "modules")
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)
    import jcns_curve
    return jcns_curve


# 同一时刻只画当前选中的约束，所以共用一个预览槽位就够，
# 点来点去也不会不断累积图像数据。
_preview_coll = None
_preview_key = None
CURVE_W, CURVE_H = 180, 110


def _curve_icon(sources):
    """当前映射的 icon_id，数值没变就不重新光栅化。

    面板会频繁重绘，每次都重算两万个浮点数纯属浪费，所以先把锚点算成缓存键。
    """
    global _preview_key
    if _preview_coll is None:
        return None
    c = _curve()
    key = c.cache_key(sources, CURVE_W, CURVE_H)
    prev = _preview_coll.get("mapping")
    if prev is None:
        prev = _preview_coll.new("mapping")
        _preview_key = None
    if key != _preview_key:
        pixels, _info = c.render(sources, CURVE_W, CURVE_H)
        prev.image_size = (CURVE_W, CURVE_H)
        prev.image_pixels_float = pixels
        _preview_key = key
    return prev.icon_id


def _classify(context):
    """返回 'ROOT'、'CONSTRAINT' 或 None。"""
    from . import get_jcns_root, get_jcns_constraint
    _, rp = get_jcns_root(context)
    if rp:
        return 'ROOT'
    _, cp = get_jcns_constraint(context)
    if cp:
        return 'CONSTRAINT'
    return None


def _active_source(p):
    if not len(p.sources):
        return None
    return p.sources[min(p.active_source_index, len(p.sources) - 1)]


def _fmt(v):
    """去掉无意义的小数尾巴：-15.0 -> -15，1.25 保留。"""
    return ("%+.0f" % v) if abs(v - round(v)) < 0.005 else ("%+.2f" % v)


# ---------------------------------------------------------------------------
# 驱动源列表
# ---------------------------------------------------------------------------

class JCNS_UL_Sources(bpy.types.UIList):
    """每行对应文件里的一个 ConstraintSource_v2 块。"""
    bl_idname = "JCNS_UL_sources"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        info = _mapping().describe(item)
        row = layout.row(align=True)
        row.label(text=str(index), icon='DRIVER')
        row.prop(item, "source_bone", text="", emboss=False)
        row.label(text=item.source_axis)
        if info['offset_at_rest']:
            row.label(text="%s°" % _fmt(info['at_rest']), icon='ERROR')


# ---------------------------------------------------------------------------
# 常驻提示面板
# ---------------------------------------------------------------------------

class JCNS_PT_Status(Panel):
    bl_label    = "RE Engine JCNS 编辑器"
    bl_idname   = "JCNS_PT_status"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS 编辑器'
    bl_order       = 0

    def draw(self, context):
        layout = self.layout
        kind = _classify(context)

        if kind is None:
            col = layout.column(align=True)
            col.label(text="未选中 JCNS 对象", icon='INFO')
            col.label(text="先导入 .jcns 文件（.102 / .35），")
            col.label(text="再选中生成的空物体。")
            layout.separator()
            layout.operator("jcns.import_file", text="导入 JCNS…", icon='IMPORT')
            return

        if kind == 'ROOT':
            from . import get_jcns_root
            obj, rp = get_jcns_root(context)
            fname = rp.source_filepath.replace('\\', '/').split('/')[-1]
            col = layout.column(align=True)
            col.label(text="根节点：%s" % obj.name, icon='EMPTY_AXIS')
            col.label(text="文件：%s" % fname, icon='FILE')
        else:
            from . import get_jcns_constraint
            obj, cp = get_jcns_constraint(context)
            col = layout.column(align=True)
            col.label(text="已选中约束", icon='CONSTRAINT_BONE')
            col.label(text=obj.name)


# ---------------------------------------------------------------------------
# 根节点面板
# ---------------------------------------------------------------------------

class JCNS_PT_Root(Panel):
    bl_label    = "JCNS 文件"
    bl_idname   = "JCNS_PT_root"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS 编辑器'
    bl_order       = 1

    @classmethod
    def poll(cls, context):
        return _classify(context) == 'ROOT'

    def draw(self, context):
        from . import get_jcns_root
        layout = self.layout
        obj, rp = get_jcns_root(context)

        box = layout.box()
        col = box.column(align=True)
        col.label(text="源文件：", icon='FILE')
        col.label(text=rp.source_filepath.replace('\\', '/').split('/')[-1])
        _GAME = {'MHW_WILDS': "怪物猎人荒野 (v102)", 'RE9': "生化危机9 / PRAGMATA (v35)"}
        col.label(text="游戏：%s" % _GAME.get(rp.detected_game, rp.detected_game),
                  icon='WORLD')

        layout.separator()
        layout.label(text="目标骨架：", icon='ARMATURE_DATA')
        layout.prop(rp, "target_armature", text="")

        layout.separator()
        row = layout.row(align=True)
        row.prop(rp, "source_combine", text="多源合并")
        layout.label(text="合并方式尚未实机验证", icon='INFO')

        col = layout.column(align=True)
        col.scale_y = 1.4
        col.operator("jcns.apply_all_drivers", text="应用全部驱动器", icon='DRIVER')
        col.operator("jcns.clear_drivers",     text="清除全部驱动器", icon='X')

        layout.separator()
        layout.operator("jcns.add_constraint", text="新增约束", icon='ADD')
        layout.operator("jcns.export_file", text="导出 JCNS", icon='EXPORT')


class JCNS_PT_RootChannels(Panel):
    """按驱动的骨骼通道分组列出约束。

    一根骨头通常每个轴一条约束，而同一个轴也可能有好几条。平铺列表会把这两件事
    都藏起来，很容易把别的约束造成的表现算到当前选中的这条头上。
    """
    bl_label    = "被驱动的骨骼"
    bl_idname   = "JCNS_PT_root_channels"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS 编辑器'
    bl_parent_id   = "JCNS_PT_root"

    def draw(self, context):
        from . import get_jcns_root, group_constraints_by_channel
        layout = self.layout
        obj, rp = get_jcns_root(context)
        m = _mapping()

        groups = group_constraints_by_channel(obj)
        if not groups:
            layout.label(text="没有 Ranges 类型的约束。", icon='INFO')
            return

        by_bone = {}
        for (bone, transform, axis), members in groups.items():
            by_bone.setdefault(bone or '???', []).append((transform, axis, members))

        active = context.active_object
        total = sum(len(mm) for v in by_bone.values() for _, _, mm in v)
        layout.label(text="%d 根骨骼 · %d 个通道 · %d 条约束"
                          % (len(by_bone), len(groups), total))

        for bone in sorted(by_bone):
            box = layout.box()
            col = box.column(align=True)
            col.label(text=bone, icon='BONE_DATA')

            for transform, axis, members in sorted(by_bone[bone],
                                                   key=lambda x: (x[0], x[1])):
                sources = [sp for e in members for sp in e.jcns_cns_props.sources]
                info = m.describe_channel(sources, rp.source_combine)

                row = col.row(align=True)
                row.alert = info['offset_at_rest']
                applied = any(e.jcns_cns_props.driver_applied for e in members)
                icon = ('ERROR' if info['offset_at_rest']
                        else 'DRIVER' if applied else 'BLANK1')
                srcs = "、".join(sorted({sp.source_bone + " " + sp.source_axis
                                        for sp in sources if sp.source_bone})) or "（无）"
                suffix = ""
                if info['offset_at_rest']:
                    suffix = "   静止 %s°" % _fmt(info['at_rest'])
                elif info['all_inert']:
                    suffix = "   （无输出）"
                row.label(text="局部 %s 轴 ← %s%s" % (axis, srcs, suffix), icon=icon)

                if len(members) > 1:
                    col.label(text="        %d 条约束合并到此通道" % len(members),
                              icon='DOT')
                for e in members:
                    sub = col.row(align=True)
                    sub.active = (e is active)
                    op = sub.operator("object.select_pattern", text="    " + e.name,
                                      emboss=False)
                    op.pattern = e.name
                    op.extend = False


# ---------------------------------------------------------------------------
# 约束面板
# ---------------------------------------------------------------------------

class JCNS_PT_Constraint(Panel):
    bl_label    = "JCNS 约束"
    bl_idname   = "JCNS_PT_constraint"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS 编辑器'
    bl_order       = 1

    @classmethod
    def poll(cls, context):
        return _classify(context) == 'CONSTRAINT'

    def draw(self, context):
        from . import get_jcns_constraint, sibling_constraints
        layout = self.layout
        obj, p = get_jcns_constraint(context)
        m = _mapping()

        ctype = p.constraint_type or 'Ranges'

        if ctype == 'JointExportGraph':
            box = layout.box()
            col = box.column(align=True)
            col.label(text="JointExportGraph 路径", icon='FILE_FOLDER')
            col.prop(p, "jxg_path", text="路径")
            return

        if ctype == 'Material':
            box = layout.box()
            col = box.column(align=True)
            col.label(text="关联骨骼", icon='BONE_DATA')
            row = col.row(align=True)
            row.label(text="骨骼：")
            row.prop(p, "target_bone", text="")
            col.separator()
            col.label(text="原始字段", icon='PREFERENCES')
            row2 = col.row(align=True)
            row2.prop(p, "mat_name_hash",     text="材质名哈希")
            row2.prop(p, "mat_property_hash", text="属性哈希")
            row3 = col.row(align=True)
            row3.prop(p, "mat_transform_type_raw", text="变换ID")
            row3.prop(p, "mat_tail_0", text="尾0")
            row3.prop(p, "mat_tail_1", text="尾1")
            row3.prop(p, "mat_tail_2", text="尾2")
            return

        if ctype != 'Ranges':
            _NAMES = {'Aim': "Aim 瞄准约束", 'RotExpression': "旋转表达式"}
            row = layout.row()
            row.alert = True
            row.label(text="类型：%s —— 暂不可编辑" % _NAMES.get(ctype, ctype),
                      icon='ERROR')
            layout.label(text="导出时会原样保留。")
            return

        # --- 目标 ---
        box = layout.box()
        col = box.column(align=True)
        col.label(text="目标", icon='OUTLINER_OB_ARMATURE')
        r = col.row()
        r.label(text="骨骼：")
        r.prop(p, "target_bone", text="")
        r = col.row()
        r.label(text="局部轴向：")
        r.prop(p, "target_axis", text="")
        r = col.row()
        r.label(text="变换：")
        r.prop(p, "transform_type", text="")

        # --- 通道共用提示 ---
        sibs = sibling_constraints(obj)
        if sibs:
            sbox = layout.box()
            scol = sbox.column(align=True)
            scol.label(text="另有 %d 条约束也在驱动 %s 的局部 %s 轴"
                            % (len(sibs), p.target_bone or '?', p.target_axis),
                       icon='INFO')
            for s in sibs:
                scol.label(text="    " + s.name, icon='DOT')
            scol.label(text="它们会合并成同一条驱动器。")

        # --- 驱动源 ---
        layout.separator()
        box_s = layout.box()
        hdr = box_s.row(align=True)
        hdr.label(text="驱动源（%d）" % len(p.sources), icon='DRIVER')
        hdr.operator("jcns.add_source", text="", icon='ADD')
        hdr.operator("jcns.remove_source", text="", icon='REMOVE')

        if not len(p.sources):
            box_s.label(text="没有驱动源，此约束不会产生任何效果。", icon='INFO')
        else:
            # 只有一个源时列表是纯浪费：下面的详情已经把骨骼和轴写了一遍。
            if len(p.sources) > 1:
                box_s.template_list("JCNS_UL_sources", "", p, "sources",
                                    p, "active_source_index",
                                    rows=min(len(p.sources), 6))
            sp = _active_source(p)
            self._draw_source(box_s, p, sp, m,
                              label=("驱动源 %d" % p.active_source_index
                                     if len(p.sources) > 1 else None))

        # Driver controls first, then tools, and only then the destructive one.
        # The delete button used to sit as a bare trash icon beside "Apply
        # Driver", where it read as "clear the driver" rather than "delete this
        # constraint".
        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("jcns.apply_single_driver",
                     text="重新应用驱动器" if p.driver_applied else "应用驱动器",
                     icon='DRIVER')
        sub = col.row(align=True)
        sub.enabled = p.driver_applied
        sub.operator("jcns.clear_single_driver", text="清除驱动器", icon='X')

        layout.separator()
        layout.operator("jcns.mirror_constraints", text="镜像到另一侧…",
                        icon='MOD_MIRROR')

        layout.separator()
        danger = layout.row()
        danger.alert = True
        danger.operator("jcns.delete_constraint", text="删除此约束", icon='TRASH')

    def _draw_source(self, layout, p, sp, m, label=None):
        col = layout.column(align=True)
        if label:
            col.label(text=label, icon='BONE_DATA')
        r = col.row()
        r.label(text="骨骼：")
        r.prop(sp, "source_bone", text="")
        r = col.row()
        r.label(text="局部轴向：")
        r.prop(sp, "source_axis", text="")

        self._draw_plain(layout, p, sp, m)
        self._draw_curve(layout, p, sp)

        col2 = layout.column(align=True)
        col2.separator()
        col2.label(text="锚点数值（局部轴，单位：度）", icon='PREFERENCES')
        h = col2.row()
        h.label(text="源局部角：")
        h.label(text="起点 A")
        h.label(text="折点 B")
        h.label(text="终点 C")
        rw = col2.row(align=True)
        rw.label(text="")
        rw.prop(sp, "from_start", text="")
        rw.prop(sp, "from_kink",  text="")
        rw.prop(sp, "from_end",   text="")

        h = col2.row()
        h.label(text="输出：")
        h.label(text="A′")
        h.label(text="B′")
        h.label(text="C′")
        rw = col2.row(align=True)
        rw.label(text="")
        rw.prop(sp, "to_start", text="")
        rw.prop(sp, "to_kink",  text="")
        rw.prop(sp, "to_end",   text="")

    def _draw_plain(self, layout, p, sp, m):
        """用大白话讲这条映射到底干什么。

        文件里锚点是按 A→B→C 存的，但静止姿态不一定落在 A 上 —— 递减区间
        （比如 [-60,-15,0]）的静止点在 C，照着数值从左往右念等于把动作念反了。
        所以这里从静止姿态出发，朝源骨骼实际能转的方向逐段描述。
        """
        d = m.plain_description(sp)
        box = layout.box()
        col = box.column(align=True)
        src = sp.source_bone or "驱动骨"
        tgt = p.target_bone or "目标骨"

        if d.get('unreachable_anchor'):
            warn = col.column(align=True)
            warn.alert = True
            warn.label(text="锚点顺序折返，终点 %s 永远取不到"
                            % d['unreachable_anchor'], icon='ERROR')
            warn.label(text="改它不会有任何效果 —— 三个源角度需按大小排列")
            warn.operator("jcns.sort_anchors", text="按源角度排序锚点",
                          icon='SORTSIZE')
            col.separator()

        if d['inert']:
            col.label(text="此约束恒无输出（输出锚点全为 0）", icon='RADIOBUT_OFF')
            return

        head = col.row()
        head.alert = d['offset_at_rest']
        if d['offset_at_rest']:
            head.label(text="静止时 %s 已偏转 %s°" % (tgt, _fmt(d['rest_output'])),
                       icon='ERROR')
        else:
            head.label(text="静止时 %s 不动" % tgt, icon='CHECKMARK')

        for leg in d['legs']:
            col.separator(factor=0.4)
            for (x0, x1, y0, y1, kind) in leg['steps']:
                if kind == 'dead':
                    col.label(text="%s 局部 %s 轴 %s° → %s°：%s 不动"
                                   % (src, sp.source_axis, _fmt(x0), _fmt(x1), tgt))
                else:
                    col.label(text="%s 局部 %s 轴 %s° → %s°：%s 的局部 %s 轴 %s° → %s°"
                                   % (src, sp.source_axis, _fmt(x0), _fmt(x1),
                                      tgt, p.target_axis, _fmt(y0), _fmt(y1)))

        if d['offset_at_rest'] and m.would_swapping_ends_help(sp):
            col.separator()
            col.operator("jcns.swap_mapto_ends",
                         text="对调输出首尾（可修正）", icon='ARROW_LEFTRIGHT')

    def _draw_curve(self, layout, p, sp):
        """画出折线。多源约束会把同通道的曲线叠在一起画。"""
        sources = list(p.sources) if len(p.sources) > 1 else [sp]
        icon = _curve_icon(sources)
        if icon is None:
            return
        box = layout.box()
        box.label(text="曲线（横轴：源骨局部轴角度　纵轴：输出）", icon='FCURVE')
        row = box.row()
        row.alignment = 'CENTER'
        row.template_icon(icon_value=icon, scale=7.0)


class JCNS_PT_ConstraintAdvanced(Panel):
    """几乎每个文件都相同、或者含义尚未逆向出来的字段。"""
    bl_label    = "高级 / 原始字段"
    bl_idname   = "JCNS_PT_constraint_advanced"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS 编辑器'
    bl_parent_id   = "JCNS_PT_constraint"
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        from . import get_jcns_constraint
        _, p = get_jcns_constraint(context)
        return p is not None and p.constraint_type == 'Ranges'

    def draw(self, context):
        from . import get_jcns_constraint
        layout = self.layout
        _, p = get_jcns_constraint(context)
        sp = _active_source(p)

        if sp is not None:
            box = layout.box()
            col = box.column(align=True)
            col.label(text="驱动源：静止四元数", icon='ORIENTATION_GIMBAL')
            r = col.row(align=True)
            r.prop(sp, "rest_quat_x", text="X")
            r.prop(sp, "rest_quat_y", text="Y")
            r.prop(sp, "rest_quat_z", text="Z")
            r.prop(sp, "rest_quat_w", text="W")

            col.separator()
            col.label(text="驱动源：原始字节", icon='PREFERENCES')
            r = col.row(align=True)
            r.prop(sp, "update_timing",    text="+24")
            r.prop(sp, "src_transform_id", text="+25")
            r.prop(sp, "unk_byte2",        text="+27")
            col.label(text="+24 / +25 含义未确认，建议不要改动", icon='INFO')
            r = col.row(align=True)
            r.prop(sp, "unknown_uint16",   text="U16(+22)")
            r.prop(sp, "unknown_uint32_2", text="U32(+28)")
            r.prop(sp, "complex_mapping_info_count", text="复杂映射数")

        box = layout.box()
        box.label(text="ConstraintInfo 原始字段", icon='PREFERENCES')
        col = box.column(align=True)
        r = col.row(align=True)
        r.prop(p, "cns_flags", text="标志位")
        icon = 'TRIA_DOWN' if p.flags_expanded else 'TRIA_RIGHT'
        r.prop(p, "flags_expanded", text="", icon=icon, emboss=False)
        col.label(text="位4 / 位5 导出时会按变换类型重算，无需手动维护", icon='INFO')
        if p.flags_expanded:
            bits = col.column(align=True)
            for attr, desc in (
                ("flag_bit_0", "位0 —— isAdd？（可自由设置）"),
                ("flag_bit_1", "位1"),
                ("flag_bit_2", "位2"),
                ("flag_bit_3", "位3"),
                ("flag_bit_4", "位4 —— 驱动骨骼（导出时按变换类型自动设置）"),
                ("flag_bit_5", "位5 —— 驱动量为旋转（导出时按变换类型自动设置）"),
                ("flag_bit_6", "位6"),
                ("flag_bit_7", "位7"),
            ):
                rb = bits.row(align=True)
                rb.prop(p, attr, text="")
                rb.label(text=desc)

        col.label(text="未知四维向量：")
        r = col.row(align=True)
        for a in ("parent_vec4_x", "parent_vec4_y", "parent_vec4_z", "parent_vec4_w"):
            r.prop(p, a, text=a[-1].upper())
        col.label(text="未知浮点对：")
        r = col.row(align=True)
        r.prop(p, "parent_float2_x", text="X")
        r.prop(p, "parent_float2_y", text="Y")
        r = col.row(align=True)
        r.prop(p, "parent_uint8_72", text="+72")
        r.prop(p, "property_hash",   text="属性哈希")
        r.prop(p, "cone_driver_info_count", text="锥形驱动数")
        col.label(text="尾部字节 [74..79]：")
        r = col.row(align=True)
        for a in ("parent_tail_0", "parent_tail_1", "parent_tail_2",
                  "parent_tail_3", "parent_tail_4", "parent_tail_5"):
            r.prop(p, a, text="")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

_classes = [
    JCNS_UL_Sources,
    JCNS_PT_Status,
    JCNS_PT_Root,
    JCNS_PT_RootChannels,
    JCNS_PT_Constraint,
    JCNS_PT_ConstraintAdvanced,
]


def register():
    global _preview_coll
    import bpy.utils.previews
    _preview_coll = bpy.utils.previews.new()
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    global _preview_coll, _preview_key
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if _preview_coll is not None:
        bpy.utils.previews.remove(_preview_coll)
        _preview_coll = None
    _preview_key = None
