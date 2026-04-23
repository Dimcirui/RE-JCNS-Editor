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
from bpy.props import StringProperty
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
    """
    Return (root_empty, root_props) for the currently active JCNS session.
    Accepts either the root Empty or any constraint Empty as the active object.
    """
    from . import (get_jcns_root, get_jcns_constraint,
                   get_jcns_root_from_constraint)

    obj, rp = get_jcns_root(context)
    if obj is not None:
        return obj, rp

    cns_obj, _ = get_jcns_constraint(context)
    if cns_obj:
        return get_jcns_root_from_constraint(cns_obj)

    return None, None


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
        _add_name(p.source_bone)
        _add_name(p.target_bone)

    # Reconstruct original_bytes stub: header block + section table at its offset
    tags = base64.b64decode(root_props.cached_file_header)
    sec_data = base64.b64decode(root_props.cached_section_table)
    orig_sec_off = struct.unpack_from('<Q', tags, 0xB0)[0]
    stub_size = max(0xF0, orig_sec_off + len(sec_data)) if orig_sec_off > 0 else 0xF0
    stub = bytearray(stub_size)
    stub[:len(tags)] = tags
    if orig_sec_off > 0:
        stub[orig_sec_off : orig_sec_off + len(sec_data)] = sec_data

    class _StubParser:
        pass

    parser = _StubParser()
    parser.constraints = []       # n_orig = 0, all constraints built from empties
    parser.hash_list = hash_list
    parser.original_bytes = bytes(stub)
    parser.filepath = root_props.source_filepath
    # Non-Range sections are not cached — they will be absent from stub exports
    parser.aim_constraints    = []
    parser.rot_expressions    = []
    parser.rot_expression_map = b''
    parser.material_cns       = []
    parser.joint_export_graph = None
    return parser


def _sync_non_range_to_parser(root_obj, parser):
    """
    Walk root_obj's children for JXG and Material empties and write
    their current property values back into the parser object so the
    writer picks up any edits the user made.
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

    for obj in root_obj.children:
        p = getattr(obj, 'jcns_cns_props', None)
        if p is None:
            continue
        ctype = p.constraint_type

        if ctype == 'JointExportGraph':
            if parser.joint_export_graph is not None:
                parser.joint_export_graph['path'] = p.source_bone

        elif ctype == 'Material':
            # Extract index from name "[MatNN] …"
            name = obj.name
            mat_idx = -1
            if name.startswith('[Mat') and ']' in name:
                try:
                    mat_idx = int(name[4:name.index(']')])
                except ValueError:
                    pass
            mat_list = getattr(parser, 'material_cns', [])
            if not (0 <= mat_idx < len(mat_list)):
                continue
            mc = mat_list[mat_idx]

            # Update joint hash index if bone name changed
            bone_name = p.target_bone.strip()
            if bone_name:
                new_idx = _ensure_hash(bone_name, parser.hash_list)
                mc['JointHashIndex'] = new_idx
                mc['JointHash'] = parser.hash_list[new_idx]

            # Rebuild raw_body (12 bytes) from editable props
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
            mc['raw_body'] = bytes(raw)


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
        'SourceCount_parent':    1,
        'Flags':                 0x30,
        'TransformType':         _TRANSFORM_STR_TO_INT.get(p.transform_type, 1),
        'ParentVec4':            (0.0, 0.0, 0.0, 1.0),
        'ParentFloat2':          (0.0, 0.0),
        'ParentUInt8_72':        0,
        'TransformAxis_parent':  tgt_ax,
        'ParentTailBytes':       b'\x00' * 6,
        # ConstraintSource_v2 preserved fields
        'ComplexMappingInfoOffset': 0,
        'ComplexMappingInfoCount':  0,
        'UnknownUInt16':            0,
        'UnkByte0':          3,
        'Interpolation':     1,
        'UnkByte2':          0,
        'UnknownUInt32_2':   0,
        # Editable fields (overwritten by _patch_constraint_from_empty)
        'SourceName':       '',
        'SourceHashIndex':  0,
        'TargetBoneName':   '',
        'source_axis':      0,    # → ConstraintSource_v2[+26]
        # target_axis → TransformAxis_parent (ConstraintInfo[+73]), set above
        'from_start':       0.0, 'from_kink':  0.0, 'from_end':  0.0,
        'to_start':         0.0, 'to_kink':    0.0, 'to_end':    0.0,
        'rest_quat_x':      0.0, 'rest_quat_y': 0.0,
        'rest_quat_z':      0.0, 'rest_quat_w': 1.0,
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
    p = empty_obj.jcns_cns_props

    # --- Source bone name ---
    new_source_name = p.source_bone.strip()
    if new_source_name:
        _ensure_modules_path()
        try:
            from hashing.mmh3.pymmh3 import hashUTF16
            new_hash = hashUTF16(new_source_name) & 0xFFFFFFFF
            found_idx = next(
                (i for i, h in enumerate(hash_list) if h == new_hash),
                None
            )
            parsed_c['SourceName'] = new_source_name
            if found_idx is not None:
                parsed_c['SourceHashIndex'] = found_idx
                print(f"[JCNS EXPORT] source_bone '{new_source_name}' "
                      f"→ hash 0x{new_hash:08x} → hash_list[{found_idx}]")
            else:
                # Hash not in existing list — writer will append it
                print(f"[JCNS EXPORT] source_bone '{new_source_name}' "
                      f"hash 0x{new_hash:08x} will be appended to hash_list")
        except Exception as exc:
            print(f"[JCNS WARN ] Could not compute hash for '{new_source_name}': {exc}")

    # --- Target bone name ---
    new_target_name = p.target_bone.strip()
    if new_target_name:
        # Writer derives TargetHash/TargetHashIndex from this name automatically
        parsed_c['TargetBoneName'] = new_target_name

    # --- Axes and mapping values ---
    parsed_c['source_axis']          = AXIS_TO_INT.get(p.source_axis, 0)
    parsed_c['TransformAxis_parent'] = AXIS_TO_INT.get(p.target_axis, 0)
    parsed_c['from_start']   = p.from_start
    parsed_c['from_kink']    = p.from_kink
    parsed_c['from_end']     = p.from_end
    parsed_c['to_start']     = p.to_start
    parsed_c['to_kink']      = p.to_kink
    parsed_c['to_end']       = p.to_end
    parsed_c['rest_quat_x']  = p.rest_quat_x
    parsed_c['rest_quat_y']  = p.rest_quat_y
    parsed_c['rest_quat_z']  = p.rest_quat_z
    parsed_c['rest_quat_w']  = p.rest_quat_w

    # --- ConstraintInfo raw fields ---
    parsed_c['Flags'] = p.cns_flags & 0xFF
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

    # --- ConstraintSource_v2 raw fields ---
    from . import INTERPOLATION_TO_INT
    parsed_c['Interpolation']            = INTERPOLATION_TO_INT.get(p.interpolation, 1)
    parsed_c['UnkByte0']                 = p.unk_byte0
    parsed_c['UnkByte2']                 = p.unk_byte2
    parsed_c['ComplexMappingInfoCount']  = p.complex_mapping_info_count
    parsed_c['UnknownUInt16']            = p.unknown_uint16
    parsed_c['UnknownUInt32_2']          = p.unknown_uint32_2


# ---------------------------------------------------------------------------
# Export Operator
# ---------------------------------------------------------------------------

class JCNS_OT_ExportFile(Operator, ExportHelper):
    """Export the selected JCNS collection back to a .jcns.102 binary file"""
    bl_idname = "jcns.export_file"
    bl_label  = "RE Engine JCNS (.jcns.102)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.jcns.102;*.jcns.29;*.jcns.35",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        obj, _ = _get_active_root(context)
        return obj is not None

    def invoke(self, context, event):
        _, rp = _get_active_root(context)
        if rp and rp.source_filepath:
            self.filepath = bpy.path.abspath(rp.source_filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import get_constraint_empties

        root_obj, root_props = _get_active_root(context)
        if root_obj is None:
            self.report({'ERROR'}, "No JCNS root Empty detected. Select the JCNS collection root.")
            return {'CANCELLED'}

        source_path = bpy.path.abspath(root_props.source_filepath)
        source_exists = os.path.isfile(source_path)

        empties = get_constraint_empties(root_obj)
        if not empties:
            self.report({'WARNING'}, "No constraint Empties found — nothing to export.")
            return {'CANCELLED'}

        # --- Build parser: re-parse source if present, else use cached stub ---
        _ensure_modules_path()
        if source_exists:
            try:
                from jcns_parser import JCNSParser
                parser = JCNSParser(source_path)
                parser.parse()
            except Exception as exc:
                self.report({'ERROR'}, f"Re-parse error: {exc}")
                return {'CANCELLED'}
        else:
            if not root_props.cached_file_header:
                self.report(
                    {'ERROR'},
                    f"Source file not found and no cached header available: {source_path}\n"
                    "Re-import the file to rebuild the cache."
                )
                return {'CANCELLED'}
            self.report({'WARNING'}, f"Source file missing — exporting from cached header data.")
            parser = _build_stub_parser(root_props, empties)

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
            writer.build_lossless()
        except Exception as exc:
            self.report({'ERROR'}, f"Writer error: {exc}")
            return {'CANCELLED'}

        # --- MD5 after write ---
        with open(out_path, 'rb') as f:
            md5_after = hashlib.md5(f.read()).hexdigest()

        if out_path != source_path:
            root_props.source_filepath = out_path

        basename = os.path.basename(out_path)
        if md5_before is None:
            self.report({'INFO'}, f"Exported '{basename}' (from cached header, no source MD5).")
        elif md5_before == md5_after:
            self.report({'INFO'}, f"Exported '{basename}' — no changes (MD5 identical).")
        else:
            self.report(
                {'INFO'},
                f"Exported '{basename}' (MD5 {md5_before[:8]}… → {md5_after[:8]}…)"
            )
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Menu hook
# ---------------------------------------------------------------------------

def _menu_export(self, context):
    obj, _ = _get_active_root(context)
    if obj is not None:
        self.layout.operator(JCNS_OT_ExportFile.bl_idname, text="RE Engine JCNS (.jcns.102)")


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
