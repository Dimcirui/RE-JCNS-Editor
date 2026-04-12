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
            col.label(text="Import a .jcns.102 file,")
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

        # File path (read-only display)
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Source File:", icon='FILE')
        col.label(text=rp.source_filepath.split('\\')[-1].split('/')[-1])

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
