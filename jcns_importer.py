"""
jcns_importer.py
----------------
Import operator for RE Engine JCNS v102 files.

Creates a green collection (JCNS_<filename>) containing:
  - One root Empty (PLAIN_AXES) with JCNSRootProperties
  - N child Empties (ARROWS), one per constraint, with JCNSConstraintProperties

Each child Empty is named:  [idx] SrcBone → TgtBone Axis
"""

import os
import sys
import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_modules_path():
    addon_dir = os.path.dirname(__file__)
    modules_dir = os.path.join(addon_dir, "modules")
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)


def _get_armature_items(self, context):
    """Dynamic EnumProperty callback: list all Armature objects in the scene."""
    items = [("NONE", "(None — skip hash resolution)", "", 'X', 0)]
    for i, obj in enumerate(context.scene.objects):
        if obj.type == 'ARMATURE':
            items.append((obj.name, obj.name, f"Use armature '{obj.name}'", 'ARMATURE_DATA', i + 1))
    return items


def _build_hash_dict(armature_obj):
    """
    Build { uint32_hash: bone_name } for every bone in the armature,
    trying both UTF-8 and UTF-16 MurmurHash3 variants.
    """
    _ensure_modules_path()
    hashing_dir = os.path.join(os.path.dirname(__file__), "modules", "hashing")
    if hashing_dir not in sys.path:
        sys.path.insert(0, hashing_dir)
    try:
        from mmh3 import pymmh3
    except ImportError:
        return {}

    hash_dict = {}
    for bone in armature_obj.data.bones:
        name = bone.name
        for fn in (pymmh3.hashUTF8, pymmh3.hashUTF16):
            try:
                h = fn(name) & 0xFFFFFFFF
                hash_dict[h] = name
                h2 = fn(name.lower()) & 0xFFFFFFFF
                hash_dict[h2] = name
            except Exception:
                pass
    return hash_dict


def _strip_ext(filename):
    """Strip JCNS version suffixes: 'foo.jcns.102' → 'foo'."""
    for ext in ('.102', '.29', '.jcns'):
        if filename.endswith(ext):
            filename = filename[:-len(ext)]
    return filename


# ---------------------------------------------------------------------------
# Core import logic (also callable from operators without a file dialog)
# ---------------------------------------------------------------------------

def do_import(filepath, context, armature_obj=None):
    """
    Parse the JCNS file and build the collection hierarchy.
    Returns (root_empty, count, error_str).  error_str is '' on success.
    """
    from . import (
        AXIS_TO_INT, INT_TO_AXIS, TRANSFORM_TYPE_MAP,
        make_constraint_empty_name,
    )

    _ensure_modules_path()
    try:
        from jcns_parser import JCNSParser
        parser = JCNSParser(filepath)
        constraints = parser.parse()
    except Exception as exc:
        return None, 0, f"Parser error: {exc}"

    # Build hash → bone-name dict if an armature was supplied
    hash_dict = _build_hash_dict(armature_obj) if armature_obj else {}

    # --- Create collection ---
    filename = _strip_ext(os.path.basename(filepath))
    coll_name = f"JCNS_{filename}"
    coll = bpy.data.collections.new(coll_name)
    coll.color_tag = 'COLOR_04'   # green
    context.scene.collection.children.link(coll)

    # --- Create root Empty ---
    root = bpy.data.objects.new(coll_name, None)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.2
    root.show_in_front = True
    coll.objects.link(root)

    root.jcns_root_props.source_filepath = filepath
    if armature_obj:
        root.jcns_root_props.target_armature = armature_obj

    # Legacy custom property for easy Outliner tag
    root["jcns_source"] = filepath

    # --- Create one child Empty per constraint ---
    for idx, c in enumerate(constraints):
        source_bone = c.get('SourceName', '')
        target_bone_name_from_file = c.get('TargetBoneName', '')

        # Resolve target bone name: try name from WStringOffset first, then hash lookup
        target_bone = target_bone_name_from_file
        if not target_bone and hash_dict:
            tgt_hash = c.get('TargetHash', 0)
            target_bone = hash_dict.get(tgt_hash, '')

        # Axis values
        src_ax_int = min(c.get('source_axis', 0), 3)
        tgt_ax_int = min(c.get('target_axis', 0), 3)
        src_ax_str = INT_TO_AXIS.get(src_ax_int, 'X')
        tgt_ax_str = INT_TO_AXIS.get(tgt_ax_int, 'X')

        # Transform type
        transform_int = c.get('TransformType', 1)
        transform_str = TRANSFORM_TYPE_MAP.get(transform_int, 'Unknown')

        # Empty name
        empty_name = make_constraint_empty_name(idx, source_bone, target_bone, tgt_ax_str)

        obj = bpy.data.objects.new(empty_name, None)
        obj.empty_display_type = 'ARROWS'
        obj.empty_display_size = 0.05
        obj.parent = root
        coll.objects.link(obj)

        # Populate JCNSConstraintProperties
        p = obj.jcns_cns_props
        p.source_bone    = source_bone
        p.target_bone    = target_bone
        p.transform_type = transform_str
        p.source_axis    = src_ax_str
        p.target_axis    = tgt_ax_str

        # Three-point piecewise mapping — all values in degrees (file stores degrees directly)
        p.from_start     = c.get('from_start',     0.0)
        p.from_kink      = c.get('from_kink',      0.0)
        p.from_end       = c.get('from_end',       0.0)
        p.to_start       = c.get('to_start',       0.0)
        p.to_kink        = c.get('to_kink',        0.0)
        p.to_end         = c.get('to_end',         0.0)

        # Rest-pose quaternion
        p.rest_quat_x = c.get('rest_quat_x', 0.0)
        p.rest_quat_y = c.get('rest_quat_y', 0.0)
        p.rest_quat_z = c.get('rest_quat_z', 0.0)
        p.rest_quat_w = c.get('rest_quat_w', 1.0)

    # --- Populate available_bones_json from all bone names in hash_list ---
    # Collect every SourceName and TargetBoneName that was decoded from the file.
    # These are exactly the bones that have entries in the hash_list, and are
    # therefore valid choices for source_bone editing.
    import json
    all_bone_names = set()
    for c in constraints:
        src = c.get('SourceName', '').strip()
        tgt = c.get('TargetBoneName', '').strip()
        if src:
            all_bone_names.add(src)
        if tgt:
            all_bone_names.add(tgt)
    root.jcns_root_props.available_bones_json = json.dumps(sorted(all_bone_names))

    return root, len(constraints), ''


# ---------------------------------------------------------------------------
# Import Operator
# ---------------------------------------------------------------------------

class JCNS_OT_ImportFile(Operator, ImportHelper):
    """Import a RE Engine JCNS joint constraint file and build an annotated collection"""
    bl_idname = "jcns.import_file"
    bl_label  = "RE Engine JCNS (.jcns.102)"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(
        default="*.jcns.*;*.jcns.102;*.jcns.29",
        options={'HIDDEN'},
    )

    # Armature selection (EnumProperty because PointerProperty is invalid on Operators)
    target_armature_name: EnumProperty(
        name="Target Armature",
        description="Select the skeleton to resolve target bone names at import",
        items=_get_armature_items,
    )

    resolve_hashes: BoolProperty(
        name="Resolve Target Bones",
        description="Try to resolve TargetHash values to bone names via the selected armature",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="JCNS Import Options", icon='SETTINGS')
        layout.separator()
        layout.prop(self, "resolve_hashes")
        col = layout.column()
        col.enabled = self.resolve_hashes
        col.prop(self, "target_armature_name", icon='ARMATURE_DATA')

    def execute(self, context):
        filepath = self.filepath
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        armature_obj = None
        if self.resolve_hashes and self.target_armature_name != "NONE":
            armature_obj = context.scene.objects.get(self.target_armature_name)

        root, count, err = do_import(filepath, context, armature_obj)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        arm_label = armature_obj.name if armature_obj else "none"
        self.report(
            {'INFO'},
            f"Imported JCNS: {count} constraint(s) → '{root.name}' (armature: {arm_label})"
        )
        # Select the root Empty
        bpy.ops.object.select_all(action='DESELECT')
        root.select_set(True)
        context.view_layer.objects.active = root
        return {'FINISHED'}

    def invoke(self, context, event):
        # Pre-select first armature in the scene
        for obj in context.scene.objects:
            if obj.type == 'ARMATURE':
                self.target_armature_name = obj.name
                break
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------------------
# Menu hook
# ---------------------------------------------------------------------------

def _menu_import(self, context):
    self.layout.operator(JCNS_OT_ImportFile.bl_idname, text="RE Engine JCNS (.jcns.102)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [JCNS_OT_ImportFile]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
