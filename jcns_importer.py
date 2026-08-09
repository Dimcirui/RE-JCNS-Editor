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
    for ext in ('.102', '.29', '.35', '.jcns'):
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
        return None, 0, f"解析失败：{exc}"

    # This is an editor, not just a viewer: a structure the writer can never
    # reproduce (checked on a fresh parse, so the source file is definitely
    # available — this is not the cached-header stub) makes the whole file
    # un-exportable, and there is no point opening something you can only
    # look at and never save back. Refuse the import outright rather than
    # letting the user edit for a while before finding out at export time.
    from jcns_validate import check_exportable
    problems = check_exportable(parser)
    if problems:
        msg = ("无法导入 —— 这个文件含有写入器无法完整还原的结构，编辑了也没法导出：\n"
               + "\n".join(f"  * {p}" for p in problems))
        return None, 0, msg

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

    # Detected game / version
    from jcns_parser import VERSION_GAME_MAP
    root.jcns_root_props.detected_game = VERSION_GAME_MAP.get(
        parser.header.get('Version', 102), 'MHW_WILDS'
    )

    # Cache structural data needed for export without source file
    import base64, struct as _struct
    raw = parser.original_bytes
    cns_info_start = parser.header.get('ConstraintSetsStart', 0xF0)
    root.jcns_root_props.cached_file_header = base64.b64encode(raw[:cns_info_start]).decode('ascii')
    orig_sec_off = _struct.unpack_from('<Q', raw, 0xB0)[0]
    layout = parser.header.get('layout', {})
    cb = layout.get('counts_base', 0xD0)
    cf = layout.get('counts_fields', {'SectionCount': (0x1A, '<B')})
    sc_off, sc_fmt = cf['SectionCount']
    sec_count = _struct.unpack_from(sc_fmt, raw, cb + sc_off)[0]
    if orig_sec_off > 0 and orig_sec_off + sec_count * 4 <= len(raw):
        sec_data = raw[orig_sec_off : orig_sec_off + sec_count * 4]
    else:
        sec_data = b'\x00\x00\x00\x00'
    root.jcns_root_props.cached_section_table = base64.b64encode(sec_data).decode('ascii')

    # Legacy custom property for easy Outliner tag
    root["jcns_source"] = filepath

    # --- Create one child Empty per constraint ---
    for idx, c in enumerate(constraints):
        file_sources = c.get('sources', [])
        target_bone_name_from_file = c.get('TargetBoneName', '')

        # Resolve target bone name: try name from WStringOffset first, then hash lookup
        target_bone = target_bone_name_from_file
        if not target_bone and hash_dict:
            tgt_hash = c.get('TargetHash', 0)
            target_bone = hash_dict.get(tgt_hash, '')

        tgt_ax_str = INT_TO_AXIS.get(min(c.get('target_axis', 0), 3), 'X')

        # Transform type
        transform_int = c.get('TransformType', 1)
        transform_str = TRANSFORM_TYPE_MAP.get(transform_int, 'Unknown')

        first = file_sources[0] if file_sources else {}
        empty_name = make_constraint_empty_name(
            idx, first.get('SourceName', ''), target_bone, tgt_ax_str,
            INT_TO_AXIS.get(min(first.get('source_axis', 0), 3), 'X'),
            max(0, len(file_sources) - 1),
        )

        obj = bpy.data.objects.new(empty_name, None)
        obj.empty_display_type = 'ARROWS'
        obj.empty_display_size = 0.05
        obj.parent = root
        coll.objects.link(obj)

        # Populate JCNSConstraintProperties
        p = obj.jcns_cns_props
        p.is_jcns_constraint = True
        p.constraint_type = 'Ranges'
        p.target_bone    = target_bone
        p.transform_type = transform_str
        p.target_axis    = tgt_ax_str

        # One entry per ConstraintSource_v2 block in the file
        p.sources.clear()
        for s in file_sources:
            sp = p.sources.add()
            sp.source_bone = s.get('SourceName', '')
            sp.source_axis = INT_TO_AXIS.get(min(s.get('source_axis', 0), 3), 'X')
            # mapping values are degrees; the file stores degrees directly
            sp.from_start  = s.get('from_start', 0.0)
            sp.from_kink   = s.get('from_kink',  0.0)
            sp.from_end    = s.get('from_end',   0.0)
            sp.to_start    = s.get('to_start',   0.0)
            sp.to_kink     = s.get('to_kink',    0.0)
            sp.to_end      = s.get('to_end',     0.0)
            sp.rest_quat_x = s.get('rest_quat_x', 0.0)
            sp.rest_quat_y = s.get('rest_quat_y', 0.0)
            sp.rest_quat_z = s.get('rest_quat_z', 0.0)
            sp.rest_quat_w = s.get('rest_quat_w', 1.0)
            sp.update_timing    = s.get('UpdateTiming', 3)
            sp.src_transform_id = s.get('SrcTransformID', 3)
            sp.unk_byte2        = s.get('UnkByte2', 0)
            sp.complex_mapping_info_count = s.get('ComplexMappingInfoCount', 0)
            sp.unknown_uint16   = s.get('UnknownUInt16', 0)
            sp.unknown_uint32_2 = s.get('UnknownUInt32_2', 0)

        # ConstraintInfo raw fields — set cns_flags (update callback syncs the 8 bits)
        p.cns_flags = c.get('Flags', 0x30)
        vec4                    = c.get('ParentVec4', (0.0, 0.0, 0.0, 1.0))
        p.parent_vec4_x, p.parent_vec4_y, p.parent_vec4_z, p.parent_vec4_w = vec4
        f2                      = c.get('ParentFloat2', (0.0, 0.0))
        p.parent_float2_x, p.parent_float2_y = f2
        p.parent_uint8_72       = c.get('ParentUInt8_72', 0)
        p.property_hash         = c.get('PropertyHash', 0)
        p.cone_driver_info_count = c.get('ConeDriverInfoCount', 0)
        tail = c.get('ParentTailBytes', b'\x00' * 6)
        p.parent_tail_0, p.parent_tail_1, p.parent_tail_2 = tail[0], tail[1], tail[2]
        p.parent_tail_3, p.parent_tail_4, p.parent_tail_5 = tail[3], tail[4], tail[5]


    # --- Non-Range read-only Empties (Aim, RotExpression, Material, JointExportGraph) ---

    for idx, ac in enumerate(parser.aim_constraints):
        src_name = hash_dict.get(ac['JointHash'],  f"0x{ac['JointHash']:08X}")
        tgt_name = hash_dict.get(ac['TargetHash'], f"0x{ac['TargetHash']:08X}")
        obj = bpy.data.objects.new(f"[Aim{idx:02d}] {src_name} → {tgt_name}", None)
        obj.empty_display_type = 'SPHERE'
        obj.empty_display_size = 0.03
        obj.parent = root
        coll.objects.link(obj)
        p2 = obj.jcns_cns_props
        p2.is_jcns_constraint = True
        p2.constraint_type = 'Aim'
        p2.target_bone = tgt_name

    for idx, re in enumerate(parser.rot_expressions):
        src_name = hash_dict.get(re['SourceJointHash'], f"0x{re['SourceJointHash']:08X}")
        tgt_name = hash_dict.get(re['JointHash'],       f"0x{re['JointHash']:08X}")
        obj = bpy.data.objects.new(f"[RotExpr{idx:02d}] {src_name} → {tgt_name}", None)
        obj.empty_display_type = 'CIRCLE'
        obj.empty_display_size = 0.03
        obj.parent = root
        coll.objects.link(obj)
        p2 = obj.jcns_cns_props
        p2.is_jcns_constraint = True
        p2.constraint_type = 'RotExpression'
        p2.target_bone = tgt_name

    for idx, mc in enumerate(parser.material_cns):
        jnt_name = hash_dict.get(mc['JointHash'], f"0x{mc['JointHash']:08X}")
        obj = bpy.data.objects.new(f"[Mat{idx:02d}] {jnt_name}", None)
        obj.empty_display_type = 'CUBE'
        obj.empty_display_size = 0.02
        obj.parent = root
        coll.objects.link(obj)
        p2 = obj.jcns_cns_props
        p2.is_jcns_constraint = True
        p2.constraint_type = 'Material'
        p2.target_bone = jnt_name
        import struct as _ms
        raw = mc['raw_body']        # 12 bytes: NameHash(4) + PropHash(4) + TransformID(1) + tail(3)
        p2.mat_name_hash        = f"0x{_ms.unpack_from('<I', raw, 0)[0]:08X}"
        p2.mat_property_hash    = f"0x{_ms.unpack_from('<I', raw, 4)[0]:08X}"
        p2.mat_transform_type_raw = raw[8]
        p2.mat_tail_0, p2.mat_tail_1, p2.mat_tail_2 = raw[9], raw[10], raw[11]

    if parser.joint_export_graph is not None:
        path = parser.joint_export_graph['path']
        obj = bpy.data.objects.new(f"[JXG] {path or '(empty)'}", None)
        obj.empty_display_type = 'IMAGE'
        obj.empty_display_size = 0.02
        obj.parent = root
        coll.objects.link(obj)
        p2 = obj.jcns_cns_props
        p2.is_jcns_constraint = True
        p2.constraint_type = 'JointExportGraph'
        p2.jxg_path = path

    # --- Populate available_bones_json from all bone names in hash_list ---
    # Collect every SourceName and TargetBoneName that was decoded from the file.
    # These are exactly the bones that have entries in the hash_list, and are
    # therefore valid choices for source_bone editing.
    import json
    all_bone_names = set()
    for c in constraints:
        tgt = c.get('TargetBoneName', '').strip()
        if tgt:
            all_bone_names.add(tgt)
        for s_ in c.get('sources', []):
            src = s_.get('SourceName', '').strip()
            if src:
                all_bone_names.add(src)
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
        default="*.jcns.102;*.jcns.29;*.jcns.35",
        options={'HIDDEN'},
    )

    # Armature selection (EnumProperty because PointerProperty is invalid on Operators)
    target_armature_name: EnumProperty(
        name="目标骨架",
        description="导入时用于把哈希还原成骨骼名的骨架",
        items=_get_armature_items,
    )

    resolve_hashes: BoolProperty(
        name="解析目标骨骼名",
        description="尝试用所选骨架把 TargetHash 还原成骨骼名",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="JCNS 导入选项", icon='SETTINGS')
        layout.separator()
        layout.prop(self, "resolve_hashes")
        col = layout.column()
        col.enabled = self.resolve_hashes
        col.prop(self, "target_armature_name", icon='ARMATURE_DATA')

    def execute(self, context):
        filepath = self.filepath
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, f"找不到文件：{filepath}")
            return {'CANCELLED'}

        armature_obj = None
        if self.resolve_hashes and self.target_armature_name != "NONE":
            armature_obj = context.scene.objects.get(self.target_armature_name)

        root, count, err = do_import(filepath, context, armature_obj)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        arm_label = armature_obj.name if armature_obj else "无"
        summary = f"已导入 {count} 条约束 → 「{root.name}」（骨架：{arm_label}）"
        self.report({'INFO'}, summary)
        # Select the root Empty, and make its collection the new working
        # collection so export is reachable without having to keep it selected.
        bpy.ops.object.select_all(action='DESELECT')
        root.select_set(True)
        context.view_layer.objects.active = root
        if root.users_collection:
            context.scene.jcns_active_collection = root.users_collection[0]
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
# Drag-and-drop file handler (Blender 4.1+)
# ---------------------------------------------------------------------------

class JCNS_FH_ImportFile(bpy.types.FileHandler):
    bl_idname = "JCNS_FH_import_file"
    bl_label = "RE Engine JCNS"
    bl_import_operator = "jcns.import_file"
    bl_file_extensions = ".102;.29;.35"

    @classmethod
    def poll_drop(cls, context):
        return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


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
    if bpy.app.version >= (4, 1, 0):
        bpy.utils.register_class(JCNS_FH_ImportFile)


def unregister():
    if bpy.app.version >= (4, 1, 0):
        bpy.utils.unregister_class(JCNS_FH_ImportFile)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
