"""
jcns_ui.py
----------
Sidebar panels for the JCNS Editor (View3D > Sidebar > JCNS Editor).

Two panel modes based on the active object:

  ROOT EMPTY selected  (has jcns_root_props.source_filepath set)
    → JCNS_PT_Root: file info, armature picker, Apply All / Clear, Export,
                    + mini-list of constraint Empties in the collection

  CONSTRAINT EMPTY selected  (has jcns_cns_props.source_bone set)
    → JCNS_PT_Constraint: source/target bone display, axis editors,
                           transform type, Mapping Limits, Unknown Floats,
                           Apply / Clear single driver

When nothing JCNS-related is selected, JCNS_PT_Status shows a hint.
"""

import bpy
from bpy.types import Panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(context):
    """Return 'ROOT', 'CONSTRAINT', or None."""
    from . import get_jcns_root, get_jcns_constraint
    _, rp = get_jcns_root(context)
    if rp:
        return 'ROOT'
    _, cp = get_jcns_constraint(context)
    if cp:
        return 'CONSTRAINT'
    return None


# ---------------------------------------------------------------------------
# Always-visible hint panel
# ---------------------------------------------------------------------------

class JCNS_PT_Status(Panel):
    bl_label    = "RE Engine JCNS Editor"
    bl_idname   = "JCNS_PT_status"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS Editor'
    bl_order       = 0

    def draw(self, context):
        layout = self.layout
        kind = _classify(context)

        if kind is None:
            col = layout.column(align=True)
            col.label(text="No JCNS object selected.", icon='INFO')
            col.label(text="Import a .jcns file (.102 / .35),")
            col.label(text="then select a JCNS Empty.")
            layout.separator()
            layout.operator("jcns.import_file", text="Import JCNS…", icon='IMPORT')
            return

        if kind == 'ROOT':
            from . import get_jcns_root
            obj, rp = get_jcns_root(context)
            fname = rp.source_filepath.replace('\\', '/').split('/')[-1]
            col = layout.column(align=True)
            col.label(text=f"Root: {obj.name}", icon='EMPTY_AXIS')
            col.label(text=f"File: {fname}", icon='FILE')
        else:
            from . import get_jcns_constraint
            obj, cp = get_jcns_constraint(context)
            col = layout.column(align=True)
            col.label(text="Constraint Selected", icon='CONSTRAINT_BONE')
            col.label(text=obj.name)


# ---------------------------------------------------------------------------
# Root Empty panel
# ---------------------------------------------------------------------------

class JCNS_PT_Root(Panel):
    bl_label    = "JCNS File"
    bl_idname   = "JCNS_PT_root"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS Editor'
    bl_order       = 1

    @classmethod
    def poll(cls, context):
        return _classify(context) == 'ROOT'

    def draw(self, context):
        from . import get_jcns_root, get_constraint_empties
        layout = self.layout
        obj, rp = get_jcns_root(context)

        # File path + game/version (read-only display)
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Source File:", icon='FILE')
        col.label(text=rp.source_filepath.split('\\')[-1].split('/')[-1])
        _GAME_LABELS = {'MHW_WILDS': "MH Wilds (v102)", 'RE9': "RE9 / PRAGMATA (v35)"}
        col.label(text=f"Game: {_GAME_LABELS.get(rp.detected_game, rp.detected_game)}", icon='WORLD')

        layout.separator()

        # Armature picker
        layout.label(text="Target Armature:", icon='ARMATURE_DATA')
        layout.prop(rp, "target_armature", text="")

        layout.separator()

        # Driver buttons
        col = layout.column(align=True)
        col.scale_y = 1.4
        col.operator("jcns.apply_all_drivers", text="Apply All Drivers", icon='DRIVER')
        col.operator("jcns.clear_drivers",     text="Clear All Drivers", icon='X')

        layout.separator()

        # Quick list of constraint Empties
        empties = get_constraint_empties(obj)
        box2 = layout.box()
        col2 = box2.column(align=True)
        col2.label(text=f"Constraints ({len(empties)}):", icon='CONSTRAINT')
        for i, e in enumerate(empties):
            p = e.jcns_cns_props
            icon = 'DRIVER' if p.driver_applied else 'BLANK1'
            row = col2.row()
            row.label(
                text=f"[{i:02d}] {p.source_bone}({p.source_axis})→{p.target_bone or '?'}({p.target_axis})",
                icon=icon,
            )

        layout.separator()

        # Add constraint button
        row3 = layout.row(align=True)
        row3.operator("jcns.add_constraint", text="Add Constraint", icon='ADD')

        layout.separator()
        layout.operator("jcns.export_file", text="Export JCNS", icon='EXPORT')


# ---------------------------------------------------------------------------
# Constraint Empty panel
# ---------------------------------------------------------------------------

class JCNS_PT_Constraint(Panel):
    bl_label    = "JCNS Constraint"
    bl_idname   = "JCNS_PT_constraint"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'JCNS Editor'
    bl_order       = 1

    @classmethod
    def poll(cls, context):
        return _classify(context) == 'CONSTRAINT'

    def draw(self, context):
        from . import get_jcns_constraint
        layout = self.layout
        obj, p = get_jcns_constraint(context)

        # --- Section type header ---
        ctype = p.constraint_type or 'Ranges'
        row_type = layout.row()
        row_type.alert = (ctype != 'Ranges')
        row_type.label(text=f"Section Type: {ctype}", icon='CONSTRAINT_BONE')
        if ctype == 'JointExportGraph':
            layout.separator()
            box = layout.box()
            col = box.column(align=True)
            col.label(text="JointExportGraph Path", icon='FILE_FOLDER')
            col.prop(p, "source_bone", text="Path")
            return

        if ctype == 'Material':
            layout.separator()
            box = layout.box()
            col = box.column(align=True)
            col.label(text="Joint Bone", icon='BONE_DATA')
            row = col.row(align=True)
            row.label(text="Joint:")
            row.prop(p, "target_bone", text="")
            col.separator()
            col.label(text="Raw Fields", icon='PREFERENCES')
            row2 = col.row(align=True)
            row2.prop(p, "mat_name_hash",     text="NameHash")
            row2.prop(p, "mat_property_hash", text="PropHash")
            row3 = col.row(align=True)
            row3.prop(p, "mat_transform_type_raw", text="TransformID")
            row3.prop(p, "mat_tail_0", text="T0")
            row3.prop(p, "mat_tail_1", text="T1")
            row3.prop(p, "mat_tail_2", text="T2")
            return

        if ctype != 'Ranges':
            layout.separator()
            layout.label(text="Not supported yet.", icon='ERROR')
            layout.label(text="Export will preserve this constraint unchanged.")
            return

        layout.separator()

        # --- Bone identity ---
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Identity", icon='BONE_DATA')

        row = col.row()
        row.label(text="Source:", icon='DRIVER')
        row.prop(p, "source_bone", text="")

        row2 = col.row()
        row2.label(text="Axis:")
        row2.prop(p, "source_axis", text="", expand=False)

        col.separator()

        row3 = col.row()
        row3.label(text="Target:", icon='OUTLINER_OB_ARMATURE')
        row3.prop(p, "target_bone", text="")

        row4 = col.row()
        row4.label(text="Axis:")
        row4.prop(p, "target_axis", text="", expand=False)

        col.separator()
        row5 = col.row()
        row5.label(text="Transform:", icon='ORIENTATION_GLOBAL')
        row5.prop(p, "transform_type", text="")

        layout.separator()

        # --- Piecewise Mapping ---
        box2 = layout.box()
        col2 = box2.column(align=True)
        col2.label(text="Piecewise Mapping  (degrees)", icon='DRIVER_ROTATIONAL_DIFFERENCE')

        # MapFrom row
        row_hdr = col2.row()
        row_hdr.label(text="MapFrom:")
        row_hdr.label(text="起点 A")
        row_hdr.label(text="折点 B")
        row_hdr.label(text="终点 C")
        row_from = col2.row(align=True)
        row_from.label(text="")
        row_from.prop(p, "from_start", text="")
        row_from.prop(p, "from_kink",  text="")
        row_from.prop(p, "from_end",   text="")

        col2.separator(factor=0.5)

        # MapTo row
        row_hdr2 = col2.row()
        row_hdr2.label(text="MapTo:")
        row_hdr2.label(text="起点 A'")
        row_hdr2.label(text="折点 B'")
        row_hdr2.label(text="终点 C'")
        row_to = col2.row(align=True)
        row_to.label(text="")
        row_to.prop(p, "to_start", text="")
        row_to.prop(p, "to_kink",  text="")
        row_to.prop(p, "to_end",   text="")

        layout.separator()

        # --- Rest-pose Quaternion (collapsed by default) ---
        box3 = layout.box()
        box3.label(text="Rest Quaternion  [0,0,0,1]", icon='ORIENTATION_GIMBAL')
        row_q = box3.row(align=True)
        row_q.prop(p, "rest_quat_x", text="X")
        row_q.prop(p, "rest_quat_y", text="Y")
        row_q.prop(p, "rest_quat_z", text="Z")
        row_q.prop(p, "rest_quat_w", text="W")

        layout.separator()

        # --- Advanced: ConstraintInfo raw fields ---
        box4 = layout.box()
        box4.label(text="ConstraintInfo (Raw)", icon='PREFERENCES')
        col4 = box4.column(align=True)
        row_flags_hdr = col4.row(align=True)
        row_flags_hdr.prop(p, "cns_flags", text="Flags")
        icon = 'TRIA_DOWN' if p.flags_expanded else 'TRIA_RIGHT'
        row_flags_hdr.prop(p, "flags_expanded", text="", icon=icon, emboss=False)
        if p.flags_expanded:
            bits_col = col4.column(align=True)
            for attr, desc in (
                ("flag_bit_0", "Bit0 — isAdd?"),
                ("flag_bit_1", "Bit1"),
                ("flag_bit_2", "Bit2"),
                ("flag_bit_3", "Bit3"),
                ("flag_bit_4", "Bit4 — isJoint?"),
                ("flag_bit_5", "Bit5"),
                ("flag_bit_6", "Bit6"),
                ("flag_bit_7", "Bit7"),
            ):
                row_b = bits_col.row(align=True)
                row_b.prop(p, attr, text="")
                row_b.label(text=desc)
        col4.label(text="UnknownVector4D:")
        row_v4 = col4.row(align=True)
        row_v4.prop(p, "parent_vec4_x", text="X")
        row_v4.prop(p, "parent_vec4_y", text="Y")
        row_v4.prop(p, "parent_vec4_z", text="Z")
        row_v4.prop(p, "parent_vec4_w", text="W")
        col4.label(text="UnknownFloat2:")
        row_f2 = col4.row(align=True)
        row_f2.prop(p, "parent_float2_x", text="X")
        row_f2.prop(p, "parent_float2_y", text="Y")
        row_misc = col4.row(align=True)
        row_misc.prop(p, "parent_uint8_72", text="+72")
        row_misc.prop(p, "property_hash",   text="PropHash")
        row_misc.prop(p, "cone_driver_info_count", text="CDrvCnt")
        col4.label(text="TailBytes [74..79]:")
        row_tail = col4.row(align=True)
        for attr in ("parent_tail_0","parent_tail_1","parent_tail_2",
                     "parent_tail_3","parent_tail_4","parent_tail_5"):
            row_tail.prop(p, attr, text="")

        layout.separator()

        # --- Advanced: ConstraintSource_v2 raw fields ---
        box5 = layout.box()
        box5.label(text="ConstraintSource (Raw)", icon='PREFERENCES')
        col5 = box5.column(align=True)
        col5.prop(p, "interpolation", text="Interpolation")
        row_s = col5.row(align=True)
        row_s.prop(p, "unk_byte0",  text="UnkByte0(+24)")
        row_s.prop(p, "unk_byte2",  text="UnkByte2(+27)")
        row_s2 = col5.row(align=True)
        row_s2.prop(p, "complex_mapping_info_count", text="CplxMapCnt")
        row_s2.prop(p, "unknown_uint16",             text="UInt16(+22)")
        col5.prop(p, "unknown_uint32_2", text="UInt32(+28)")

        layout.separator()

        # --- Driver buttons ---
        driver_row = layout.row(align=True)
        driver_row.scale_y = 1.3
        op_text = "Reapply Driver" if p.driver_applied else "Apply Driver"
        driver_row.operator("jcns.apply_single_driver", text=op_text, icon='DRIVER')
        driver_row.operator("jcns.delete_constraint", text="", icon='TRASH')


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [
    JCNS_PT_Status,
    JCNS_PT_Root,
    JCNS_PT_Constraint,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
