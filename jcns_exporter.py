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
    if new_source_name and new_source_name != parsed_c.get('SourceName', ''):
        _ensure_modules_path()
        try:
            from hashing.mmh3.pymmh3 import hashUTF16
            new_hash = hashUTF16(new_source_name) & 0xFFFFFFFF
            found_idx = next(
                (i for i, h in enumerate(hash_list) if h == new_hash),
                None
            )
            if found_idx is not None:
                parsed_c['SourceName']      = new_source_name
                parsed_c['SourceHashIndex'] = found_idx
                print(f"[JCNS EXPORT] source_bone '{new_source_name}' "
                      f"→ hash 0x{new_hash:08x} → hash_list[{found_idx}]")
            else:
                # Hash not in existing list — writer will append it
                parsed_c['SourceName'] = new_source_name
                print(f"[JCNS EXPORT] source_bone '{new_source_name}' "
                      f"hash 0x{new_hash:08x} will be appended to hash_list")
        except Exception as exc:
            print(f"[JCNS WARN ] Could not compute hash for '{new_source_name}': {exc}")

    # --- Target bone name ---
    new_target_name = p.target_bone.strip()
    if new_target_name and new_target_name != parsed_c.get('TargetBoneName', ''):
        # Just update the name; writer recomputes hash and index from it
        parsed_c['TargetBoneName'] = new_target_name
        print(f"[JCNS EXPORT] target_bone changed → '{new_target_name}'")

    # --- Axes and mapping values ---
    parsed_c['source_axis']  = AXIS_TO_INT.get(p.source_axis, 0)
    parsed_c['target_axis']  = AXIS_TO_INT.get(p.target_axis, 0)
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


# ---------------------------------------------------------------------------
# Export Operator
# ---------------------------------------------------------------------------

class JCNS_OT_ExportFile(Operator, ExportHelper):
    """Export the selected JCNS collection back to a .jcns.102 binary file"""
    bl_idname = "jcns.export_file"
    bl_label  = "RE Engine JCNS (.jcns.102)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".102"
    filter_glob: StringProperty(
        default="*.jcns.*;*.jcns.102",
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
        if not os.path.isfile(source_path):
            self.report(
                {'ERROR'},
                f"Original source file not found: {source_path}\n"
                "Lossless export requires the original file for structural reference."
            )
            return {'CANCELLED'}

        empties = get_constraint_empties(root_obj)
        if not empties:
            self.report({'WARNING'}, "No constraint Empties found — nothing to export.")
            return {'CANCELLED'}

        # --- Re-parse original for structural skeleton ---
        _ensure_modules_path()
        try:
            from jcns_parser import JCNSParser
            parser = JCNSParser(source_path)
            parser.parse()
        except Exception as exc:
            self.report({'ERROR'}, f"Re-parse error: {exc}")
            return {'CANCELLED'}

        if len(parser.constraints) != len(empties):
            self.report(
                {'WARNING'},
                f"Collection has {len(empties)} constraint(s) but original file "
                f"has {len(parser.constraints)}. Count mismatch — export may be lossy."
            )

        # --- Patch editable fields from Blender into parsed dicts ---
        for parsed_c, empty in zip(parser.constraints, empties):
            _patch_constraint_from_empty(parsed_c, empty, parser.hash_list)

        # --- MD5 before write ---
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
        if md5_before == md5_after:
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
