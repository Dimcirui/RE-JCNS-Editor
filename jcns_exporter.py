"""
jcns_exporter.py
----------------
Export operator for RE Engine JCNS v102 files.

Strategy:
  1. Detect the JCNS root Empty from the active object.
  2. Re-parse the original source file (structural skeleton, hash list, etc.).
  3. Gather constraint Empties from the collection, sorted by index prefix.
  4. For each constraint, patch the parsed dict with current Blender values.
  5. Call JCNSWriter.build_lossless() to write the output.
"""

import os
import sys
import hashlib
import bpy
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_modules_path():
    addon_dir = os.path.dirname(__file__)
    modules_dir = os.path.join(addon_dir, "modules")
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)


def _get_active_root(context):
    """(root_empty, root_props) to export — see __init__.get_export_root()."""
    from . import get_export_root
    return get_export_root(context)


def _build_stub_parser(root_props, empties):
    """
    Reconstruct a minimal parser-like object from cached data when the source
    file is unavailable.  All constraints are treated as 'new' (n_orig = 0).
    """
    import base64, struct

    _ensure_modules_path()
    hashing_dir = os.path.join(os.path.dirname(__file__), "modules", "hashing")
    if hashing_dir not in sys.path:
        sys.path.insert(0, hashing_dir)
    from mmh3.pymmh3 import hashUTF16

    # Rebuild hash_list from all bone names referenced by current empties
    hash_list = []
    def _add_name(name):
        if not name:
            return
        h = hashUTF16(name) & 0xFFFFFFFF
        if h not in hash_list:
            hash_list.append(h)

    for empty in empties:
        p = empty.jcns_cns_props
        _add_name(p.target_bone)
        for s in p.sources:
            _add_name(s.source_bone)

    # Reconstruct original_bytes stub: header block + section table at its offset
    tags = base64.b64decode(root_props.cached_file_header)
    sec_data = base64.b64decode(root_props.cached_section_table)
    orig_sec_off = struct.unpack_from('<Q', tags, 0xB0)[0]
    stub_size = max(0xF0, orig_sec_off + len(sec_data)) if orig_sec_off > 0 else 0xF0
    stub = bytearray(stub_size)
    stub[:len(tags)] = tags
    if orig_sec_off > 0:
        stub[orig_sec_off : orig_sec_off + len(sec_data)] = sec_data

    # Decode just the counts region of the cached header (Version + the
    # version-specific counts_fields table) so check_exportable() can still see
    # e.g. SkinConstraintCount/AimConstraintCount even without the source file.
    # The DataEntry pointer chase that the real parser does isn't needed here —
    # only skip it, don't guess at it.
    from jcns_parser import LAYOUTS
    header = {}
    try:
        version = struct.unpack_from('<I', tags, 0)[0]
        layout = LAYOUTS.get(version)
        if layout is not None:
            header['Version'] = version
            cb = layout['counts_base']
            for field, (off, fmt) in layout['counts_fields'].items():
                header[field] = struct.unpack_from(fmt, tags, cb + off)[0]
    except struct.error:
        header = {}   # cached header shorter than expected — leave counts unknown

    class _StubParser:
        pass

    parser = _StubParser()
    parser.constraints = []       # n_orig = 0, all constraints built from empties
    parser.hash_list = hash_list
    parser.original_bytes = bytes(stub)
    parser.filepath = root_props.source_filepath
    parser.header = header
    parser.is_stub = True
    # Non-Range sections are not cached — they will be absent from stub exports
    parser.aim_constraints    = []
    parser.rot_expressions    = []
    parser.rot_expression_map = b''
    parser.material_cns       = []
    parser.joint_export_graph = None
    return parser


def _sync_non_range_to_parser(root_obj, parser):
    """
    Rebuild parser.material_cns / parser.joint_export_graph entirely from the
    cached properties on the Material / JointExportGraph child Empties.

    Blender's copy is authoritative: unlike RotExpression/Aim (which have no
    editable backing store and can only be reproduced by re-parsing the source
    file), every field the writer needs for Material and JXG already round-trips
    through jcns_cns_props. Rebuilding from scratch — rather than patching a
    pre-existing parser.material_cns entry by index — means this also works when
    exporting from the cached-header stub (no source file), and means deleting a
    Material/JXG Empty in Blender removes it from the export too.
    """
    import struct

    hashing_dir = os.path.join(os.path.dirname(__file__), "modules", "hashing")
    if hashing_dir not in sys.path:
        sys.path.insert(0, hashing_dir)
    from mmh3.pymmh3 import hashUTF16

    def _ensure_hash(name, hash_list):
        """Return index of name's MurmurHash3 in hash_list, appending if missing."""
        h = hashUTF16(name) & 0xFFFFFFFF
        for i, v in enumerate(hash_list):
            if v == h:
                return i
        idx = len(hash_list)
        hash_list.append(h)
        return idx

    def _mat_index(obj):
        name = obj.name
        if name.startswith('[Mat') and ']' in name:
            try:
                return int(name[4:name.index(']')])
            except ValueError:
                pass
        return 9999

    mat_empties = sorted(
        (o for o in root_obj.children
         if getattr(o, 'jcns_cns_props', None)
         and o.jcns_cns_props.constraint_type == 'Material'),
        key=_mat_index)

    mat_entries = []
    for obj in mat_empties:
        p = obj.jcns_cns_props
        bone_name = p.target_bone.strip()
        joint_idx = _ensure_hash(bone_name, parser.hash_list) if bone_name else 0

        raw = bytearray(12)
        try:
            struct.pack_into('<I', raw, 0, int(p.mat_name_hash,     16) & 0xFFFFFFFF)
        except (ValueError, TypeError):
            pass
        try:
            struct.pack_into('<I', raw, 4, int(p.mat_property_hash, 16) & 0xFFFFFFFF)
        except (ValueError, TypeError):
            pass
        raw[8]  = p.mat_transform_type_raw & 0xFF
        raw[9]  = p.mat_tail_0 & 0xFF
        raw[10] = p.mat_tail_1 & 0xFF
        raw[11] = p.mat_tail_2 & 0xFF

        mat_entries.append({
            'JointHashIndex': joint_idx,
            'JointHash':      parser.hash_list[joint_idx],
            'raw_body':       bytes(raw),
        })
    parser.material_cns = mat_entries

    jxg_obj = next((o for o in root_obj.children
                     if getattr(o, 'jcns_cns_props', None)
                     and o.jcns_cns_props.constraint_type == 'JointExportGraph'), None)
    parser.joint_export_graph = {'path': jxg_obj.jcns_cns_props.jxg_path} if jxg_obj else None


_TRANSFORM_STR_TO_INT = {
    'Translation': 0, 'Rotation': 1, 'Scale': 2, 'BlendShape': 3,
    'Material': 7, 'Unknown': 4,
}


def _make_default_constraint_dict(empty_obj):
    """
    Build a minimal constraint dict for a newly-added constraint Empty
    (one that has no corresponding entry in the original parsed file).
    All preserved/unknown fields are set to safe defaults that match what
    the writer expects; the editable fields will be overwritten immediately
    afterwards by _patch_constraint_from_empty().
    """
    from . import AXIS_TO_INT
    p = empty_obj.jcns_cns_props
    tgt_ax = AXIS_TO_INT.get(p.target_axis, 0)
    return {
        # ConstraintInfo preserved fields
        'ConeDriverInfoOffset':  0,
        'PropertyOffset':        0,
        'PropertyHash':          0,
        'ConeDriverInfoCount':   0,
        'Flags':                 0x30,
        'TransformType':         _TRANSFORM_STR_TO_INT.get(p.transform_type, 1),
        'ParentVec4':            (0.0, 0.0, 0.0, 1.0),
        'ParentFloat2':          (0.0, 0.0),
        'ParentUInt8_72':        0,
        'TransformAxis_parent':  tgt_ax,
        'ParentTailBytes':       b'\x00' * 6,
        'TargetBoneName':        '',
        # Sources are built entirely by _patch_constraint_from_empty()
        'sources':               [],
    }


def _patch_constraint_from_empty(parsed_c, empty_obj, hash_list):
    """
    Overwrite the editable fields in the parsed constraint dict with
    values from the Empty's JCNSConstraintProperties.

    Source bone: if changed, hash_list is searched for the MurmurHash3 value
    and SourceHashIndex is updated (or the hash is appended by the writer).

    Target bone: if changed, TargetBoneName is updated; the writer recomputes
    TargetHash and TargetHashIndex from the name automatically.
    """
    from . import AXIS_TO_INT
    _ensure_modules_path()
    try:
        from hashing.mmh3.pymmh3 import hashUTF16
    except Exception:
        hashUTF16 = None
    p = empty_obj.jcns_cns_props

    # --- Target bone name (writer recomputes TargetHash/TargetHashIndex from it) ---
    new_target_name = p.target_bone.strip()
    if new_target_name:
        parsed_c['TargetBoneName'] = new_target_name

    # --- Target axis lives in ConstraintInfo[+73], not in any source block ---
    parsed_c['TransformAxis_parent'] = AXIS_TO_INT.get(p.target_axis, 0)

    # --- Sources: rebuild the whole list from the UI collection ---
    # Rebuilding rather than patching in place means added/removed sources are
    # handled for free, and SourceCount can never disagree with the actual data.
    old_sources = parsed_c.get('sources', [])
    new_sources = []
    for i, sp in enumerate(p.sources):
        # Carry over opaque fields from the matching original source when there is one
        base = dict(old_sources[i]) if i < len(old_sources) else {'ComplexMappingInfoOffset': 0}
        name = sp.source_bone.strip()
        base['SourceName'] = name
        base.setdefault('SourceHashIndex', 0)
        if name and hashUTF16 is not None:
            h = hashUTF16(name) & 0xFFFFFFFF
            found = next((j for j, hv in enumerate(hash_list) if hv == h), None)
            if found is not None:
                base['SourceHashIndex'] = found   # else: writer appends the new hash
        base['source_axis']     = AXIS_TO_INT.get(sp.source_axis, 0)
        base['from_start']      = sp.from_start
        base['from_kink']       = sp.from_kink
        base['from_end']        = sp.from_end
        base['to_start']        = sp.to_start
        base['to_kink']         = sp.to_kink
        base['to_end']          = sp.to_end
        base['rest_quat_x']     = sp.rest_quat_x
        base['rest_quat_y']     = sp.rest_quat_y
        base['rest_quat_z']     = sp.rest_quat_z
        base['rest_quat_w']     = sp.rest_quat_w
        base['UpdateTiming']    = sp.update_timing
        base['SrcTransformID']  = sp.src_transform_id
        base['UnkByte2']        = sp.unk_byte2
        base['UnknownUInt16']   = sp.unknown_uint16
        base['UnknownUInt32_2'] = sp.unknown_uint32_2
        base['ComplexMappingInfoCount'] = sp.complex_mapping_info_count
        new_sources.append(base)
    parsed_c['sources'] = new_sources


    # --- ConstraintInfo raw fields ---
    # Bits 4 and 5 are redundant with the transform type (unanimous across all
    # 19884 shipped constraints), so recompute them rather than trusting the raw
    # field — otherwise changing a constraint's transform type would silently
    # leave the flags describing the old one.
    from .modules_shim import get_flags
    parsed_c['Flags'] = get_flags().apply_derived_bits(p.cns_flags, p.transform_type)
    parsed_c['ParentVec4']          = (p.parent_vec4_x, p.parent_vec4_y,
                                       p.parent_vec4_z, p.parent_vec4_w)
    parsed_c['ParentFloat2']        = (p.parent_float2_x, p.parent_float2_y)
    parsed_c['ParentUInt8_72']      = p.parent_uint8_72
    parsed_c['PropertyHash']        = p.property_hash
    parsed_c['ConeDriverInfoCount'] = p.cone_driver_info_count
    parsed_c['ParentTailBytes']     = bytes([
        p.parent_tail_0, p.parent_tail_1, p.parent_tail_2,
        p.parent_tail_3, p.parent_tail_4, p.parent_tail_5,
    ])


# ---------------------------------------------------------------------------
# Export Operator
# ---------------------------------------------------------------------------

class JCNS_OT_ExportFile(Operator, ExportHelper):
    """Export the selected JCNS collection back to a .jcns.102 binary file"""
    bl_idname = "jcns.export_file"
    bl_label  = "RE Engine JCNS (.jcns.*)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.jcns.102;*.jcns.29;*.jcns.35",
        options={'HIDDEN'},
    )
    clean_hashes: BoolProperty(
        name="清除冗余哈希",
        description="删除已无约束引用的哈希。不勾选则保留原文件的全部哈希",
        default=False
    )

    @classmethod
    def poll(cls, context):
        obj, _ = _get_active_root(context)
        return obj is not None

    def invoke(self, context, event):
        _, rp = _get_active_root(context)
        if rp:
            # Dynamically set the auto-append extension based on detected game version
            if rp.detected_game == 'RE9':
                self.filename_ext = ".jcns.35"
            else:
                self.filename_ext = ".jcns.102"

            if rp.source_filepath:
                self.filepath = bpy.path.abspath(rp.source_filepath)
        
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def check(self, context):
        change_ext = False
        filepath = self.filepath
        if filepath != "":
            ext = self.filename_ext
            if not filepath.lower().endswith(ext.lower()):
                if filepath.lower().endswith(".jcns"):
                    self.filepath = filepath + ext.replace(".jcns", "")
                else:
                    self.filepath = filepath + ext
                change_ext = True
        return change_ext

    def execute(self, context):
        from . import get_constraint_empties

        root_obj, root_props = _get_active_root(context)
        if root_obj is None:
            self.report({'ERROR'}, "未检测到 JCNS 根节点，请先选中 JCNS 集合的根空物体。")
            return {'CANCELLED'}

        source_path = bpy.path.abspath(root_props.source_filepath)
        source_exists = os.path.isfile(source_path)

        empties = get_constraint_empties(root_obj)
        if not empties:
            self.report({'WARNING'}, "没有找到任何约束，无内容可导出。")
            return {'CANCELLED'}

        # --- Build parser: re-parse source if present, else use cached stub ---
        _ensure_modules_path()
        if source_exists:
            try:
                from jcns_parser import JCNSParser
                parser = JCNSParser(source_path)
                parser.parse()
            except Exception as exc:
                self.report({'ERROR'}, f"重新解析源文件失败：{exc}")
                return {'CANCELLED'}
        else:
            if not root_props.cached_file_header:
                self.report(
                    {'ERROR'},
                    f"源文件不存在，也没有缓存的文件头：{source_path}\n"
                    "请重新导入该文件以重建缓存。"
                )
                return {'CANCELLED'}
            self.report({'WARNING'}, "源文件缺失，将使用导入时缓存的文件头导出。")
            parser = _build_stub_parser(root_props, empties)

        # --- Refuse to write a file the writer cannot faithfully reproduce ---
        from jcns_validate import check_exportable, format_problems
        problems = check_exportable(parser)
        if problems:
            msg = format_problems(problems, os.path.basename(source_path))
            print("[JCNS EXPORT] " + msg)
            self.report({'ERROR'}, msg.replace('\n', '  '))
            return {'CANCELLED'}

        n_orig = len(parser.constraints)
        n_curr = len(empties)

        # --- Build the final constraint list (one entry per Empty) ---
        final_constraints = []
        for i, empty in enumerate(empties):
            if i < n_orig:
                parsed_c = parser.constraints[i]
            else:
                parsed_c = _make_default_constraint_dict(empty)
                print(f"[JCNS EXPORT] New constraint [{i:02d}] '{empty.name}' — using defaults")
            _patch_constraint_from_empty(parsed_c, empty, parser.hash_list)
            final_constraints.append(parsed_c)

        parser.constraints = final_constraints

        # Sync editable JXG / Material empty values back into parser
        _sync_non_range_to_parser(root_obj, parser)

        # --- MD5 before write (only if source file exists) ---
        md5_before = None
        if source_exists:
            with open(source_path, 'rb') as f:
                md5_before = hashlib.md5(f.read()).hexdigest()

        # --- Write ---
        out_path = self.filepath
        try:
            from jcns_writer import JCNSWriter
            writer = JCNSWriter(parser, out_path)
            writer.build_lossless(clean_hashes=self.clean_hashes)
        except Exception as exc:
            self.report({'ERROR'}, f"写入失败：{exc}")
            return {'CANCELLED'}

        # --- MD5 after write ---
        with open(out_path, 'rb') as f:
            md5_after = hashlib.md5(f.read()).hexdigest()

        if out_path != source_path:
            root_props.source_filepath = out_path

        basename = os.path.basename(out_path)
        if md5_before is None:
            self.report({'INFO'}, f"已导出「{basename}」（基于缓存文件头，无法比对 MD5）。")
        elif md5_before == md5_after:
            self.report({'INFO'}, f"已导出「{basename}」—— 内容无变化（MD5 相同）。")
        else:
            self.report(
                {'INFO'},
                f"已导出「{basename}」（MD5 {md5_before[:8]}… → {md5_after[:8]}…）"
            )
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Menu hook
# ---------------------------------------------------------------------------

def _menu_export(self, context):
    obj, _ = _get_active_root(context)
    if obj is not None:
        self.layout.operator(JCNS_OT_ExportFile.bl_idname, text="RE Engine JCNS (.jcns.*)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [JCNS_OT_ExportFile]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
