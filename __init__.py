import bpy
from bpy.props import (
    StringProperty,
    IntProperty,
    FloatProperty,
    BoolProperty,
    EnumProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup

bl_info = {
    "name": "RE Engine JCNS Editor",
    "author": "JCNS Reverse Engineering Project",
    "version": (0, 3, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > JCNS Editor | File > Import/Export",
    "description": (
        "Import, edit, and export RE Engine JCNS joint constraint files "
        "for Monster Hunter Wilds (v102). Each imported file creates a green "
        "collection with one Empty per constraint."
    ),
    "category": "Animation",
}


# ---------------------------------------------------------------------------
# Constants shared across modules
# ---------------------------------------------------------------------------

AXIS_ITEMS = [
    ('X', "X", "X axis (or quaternion X component)"),
    ('Y', "Y", "Y axis (or quaternion Y component)"),
    ('Z', "Z", "Z axis (or quaternion Z component)"),
    ('W', "W", "W quaternion component (not yet supported as driver target)"),
]

TRANSFORM_ITEMS = [
    ('Location',    "Location",    "Translational constraint"),
    ('Rotation',    "Rotation",    "Rotational constraint (most common)"),
    ('Scale',       "Scale",       "Scale constraint"),
    ('BlendShape',  "BlendShape",  "Blend-shape / morph target"),
    ('Material',    "Material",    "Material property drive"),
    ('Unknown',     "Unknown",     "Unrecognised transform type"),
]

AXIS_TO_INT = {'X': 0, 'Y': 1, 'Z': 2, 'W': 3}
INT_TO_AXIS = {0: 'X', 1: 'Y', 2: 'Z', 3: 'W'}

TRANSFORM_TYPE_MAP = {
    0: 'Location', 1: 'Rotation', 2: 'Scale', 3: 'BlendShape',
    4: 'Unknown', 5: 'Unknown', 7: 'Material', 8: 'Material',
    9: 'Material', 10: 'Material', 11: 'Unknown',
}


# ---------------------------------------------------------------------------
# Search callback for source_bone (populated from hash_list at import time)
# ---------------------------------------------------------------------------

def _search_bone_names(context, edit_text):
    """Return bone names from available_bones_json that match edit_text (case-insensitive)."""
    import json
    obj = context.active_object
    if obj is None:
        return []
    root_obj, root_props = get_jcns_root_from_constraint(obj)
    if root_props is None:
        return []
    try:
        names = json.loads(root_props.available_bones_json or '[]')
    except Exception:
        return []
    needle = edit_text.lower()
    return [n for n in names if needle in n.lower()]


def _search_source_bone(self, context, edit_text):
    return _search_bone_names(context, edit_text)


def _search_target_bone(self, context, edit_text):
    return _search_bone_names(context, edit_text)


# ---------------------------------------------------------------------------
# Property Group: attached to each constraint Empty child object
# ---------------------------------------------------------------------------

class JCNSConstraintProperties(PropertyGroup):
    """
    Stored on every per-constraint child Empty inside a JCNS collection.
    The Empty's name is kept as '[idx] SrcBone → TgtBone Axis' by the addon.
    """

    # --- Identity ---
    source_bone: StringProperty(
        name="Source Bone",
        description="Driving bone name. Type to search bones available in this file's hash list.",
        default="",
        search=_search_source_bone,
        search_options={'SUGGESTION', 'SORT'},
    )
    target_bone: StringProperty(
        name="Target Bone",
        description="Driven bone name. Type to search bones available in this file's hash list.",
        default="",
        search=_search_target_bone,
        search_options={'SUGGESTION', 'SORT'},
    )
    transform_type: EnumProperty(
        name="Transform Type",
        description="Kind of constraint (Rotation, Location, Scale, …)",
        items=TRANSFORM_ITEMS,
        default='Rotation',
    )

    # --- Axes (editable — exported back to file) ---
    source_axis: EnumProperty(
        name="Source Axis",
        description="Which axis of the source bone is read",
        items=AXIS_ITEMS,
        default='X',
    )
    target_axis: EnumProperty(
        name="Target Axis",
        description="Which axis of the target bone is driven",
        items=AXIS_ITEMS,
        default='X',
    )

    # --- Three-point piecewise mapping (editable — exported back to file) ---
    # File layout: two range structs {start, kink, end} × 2, then rest-pose quaternion.
    # Three anchor points define a two-segment piecewise linear transfer function:
    #   Seg 1: source [from_start → from_kink]  maps output [to_start → to_kink]   slope k1
    #   Seg 2: source [from_kink  → from_end]   maps output [to_kink  → to_end]    slope k2
    # to_kink is always 0.0 in all observed files (kink point anchored at zero output).
    from_start: FloatProperty(
        name="From 起点",
        description="MapFrom 点A — 第一段起始锚点（源角度，单位度）",
        default=0.0, precision=2, step=10,
    )
    from_kink: FloatProperty(
        name="From 折点",
        description="MapFrom 点B — 两段斜率的分界折点（源角度，单位度）",
        default=0.0, precision=2, step=10,
    )
    from_end: FloatProperty(
        name="From 终点",
        description="MapFrom 点C — 第二段终止锚点，源骨骼最大偏转角（单位度）",
        default=0.0, precision=2, step=10,
    )
    to_start: FloatProperty(
        name="To 起点",
        description="MapTo 点A — 对应 from_start 的输出值（1:1 穿越型时等于 from_start）",
        default=0.0, precision=2, step=10,
    )
    to_kink: FloatProperty(
        name="To 折点",
        description="MapTo 点B — 折点处的输出值（已验证：引擎读取此值；观测文件中恒为 0.0）",
        default=0.0, precision=2, step=10,
    )
    to_end: FloatProperty(
        name="To 终点",
        description="MapTo 点C — 目标骨骼最大输出旋转量（单位度）",
        default=0.0, precision=2, step=10,
    )

    # --- Rest-pose quaternion (editable — exported; always [0,0,0,1] in all observed files) ---
    rest_quat_x: FloatProperty(name="Quat X", default=0.0, precision=5)
    rest_quat_y: FloatProperty(name="Quat Y", default=0.0, precision=5)
    rest_quat_z: FloatProperty(name="Quat Z", default=0.0, precision=5)
    rest_quat_w: FloatProperty(name="Quat W", default=1.0, precision=5)

    # --- Driver state (runtime, not exported) ---
    driver_applied: BoolProperty(
        name="Driver Applied",
        description="Whether a Blender driver is currently active for this constraint",
        default=False,
    )


# ---------------------------------------------------------------------------
# Property Group: attached to the root Empty of each JCNS collection
# ---------------------------------------------------------------------------

def _armature_poll(self, obj):
    return obj.type == 'ARMATURE'


class JCNSRootProperties(PropertyGroup):
    """
    Stored on the root Empty of a JCNS collection.
    The collection contains one root Empty + N per-constraint Empties.
    """
    source_filepath: StringProperty(
        name="Source File",
        description="Absolute path to the original .jcns.102 file",
        subtype='FILE_PATH',
        default="",
    )
    target_armature: PointerProperty(
        name="Target Armature",
        description="Armature whose bones receive the driven constraints",
        type=bpy.types.Object,
        poll=_armature_poll,
    )
    available_bones_json: StringProperty(
        name="Available Bones (JSON)",
        description="JSON list of bone names present in this file's hash_list (set at import, used for source_bone autocomplete)",
        default="[]",
    )


# ---------------------------------------------------------------------------
# Helpers: classify active object
# ---------------------------------------------------------------------------

def get_jcns_root(context):
    """Return (obj, jcns_root_props) if active object is a JCNS root Empty, else (None, None)."""
    obj = context.active_object
    if obj is None:
        return None, None
    props = getattr(obj, 'jcns_root_props', None)
    if props and props.source_filepath:
        return obj, props
    return None, None


def get_jcns_constraint(context):
    """Return (obj, jcns_cns_props) if active object is a JCNS constraint Empty, else (None, None)."""
    obj = context.active_object
    if obj is None:
        return None, None
    props = getattr(obj, 'jcns_cns_props', None)
    if props and props.source_bone:
        return obj, props
    return None, None


def get_jcns_root_from_constraint(constraint_empty):
    """Walk up to the parent Empty to find the root, with collection fallback for legacy files."""
    parent = constraint_empty.parent
    if parent is not None:
        root_props = getattr(parent, 'jcns_root_props', None)
        if root_props and root_props.source_filepath:
            return parent, root_props

    # Fallback: flat collection search (legacy imports without parent-child hierarchy)
    for coll in constraint_empty.users_collection:
        for obj in coll.objects:
            if obj == constraint_empty:
                continue
            root_props = getattr(obj, 'jcns_root_props', None)
            if root_props and root_props.source_filepath:
                return obj, root_props
    return None, None


def get_constraint_empties(root_empty):
    """
    Return an ordered list of constraint Empty objects under the root.
    Prefers direct children (parent-child hierarchy); falls back to flat
    collection search for legacy imports.
    Sorted by the numeric prefix '[N]' in their names.
    """
    empties = []
    for obj in root_empty.children:
        cns_props = getattr(obj, 'jcns_cns_props', None)
        if cns_props and cns_props.source_bone:
            empties.append(obj)

    # Fallback: flat collection search (legacy imports without parent-child hierarchy)
    if not empties:
        for coll in root_empty.users_collection:
            for obj in coll.objects:
                if obj == root_empty:
                    continue
                cns_props = getattr(obj, 'jcns_cns_props', None)
                if cns_props and cns_props.source_bone:
                    empties.append(obj)

    def _sort_key(obj):
        name = obj.name
        if name.startswith('['):
            try:
                return int(name[1:name.index(']')])
            except (ValueError, IndexError):
                pass
        return 9999

    return sorted(empties, key=_sort_key)


def make_constraint_empty_name(idx, source_bone, target_bone, target_axis):
    """Generate the canonical display name for a constraint Empty."""
    ax = target_axis if isinstance(target_axis, str) else INT_TO_AXIS.get(target_axis, 'X')
    return f"[{idx:02d}] {source_bone} → {target_bone or '???'} {ax}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from . import jcns_operators
from . import jcns_importer
from . import jcns_exporter
from . import jcns_ui
from . import jcns_drivers

_classes = [
    JCNSConstraintProperties,
    JCNSRootProperties,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.jcns_root_props = PointerProperty(type=JCNSRootProperties)
    bpy.types.Object.jcns_cns_props  = PointerProperty(type=JCNSConstraintProperties)

    jcns_operators.register()
    jcns_importer.register()
    jcns_exporter.register()
    jcns_ui.register()
    jcns_drivers.register()


def unregister():
    jcns_drivers.unregister()
    jcns_ui.unregister()
    jcns_exporter.unregister()
    jcns_importer.unregister()
    jcns_operators.unregister()

    del bpy.types.Object.jcns_cns_props
    del bpy.types.Object.jcns_root_props

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
