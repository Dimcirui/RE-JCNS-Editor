import bpy
from bpy.props import (
    StringProperty,
    IntProperty,
    FloatProperty,
    BoolProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
)
from bpy.types import PropertyGroup

bl_info = {
    "name": "RE Engine JCNS Editor",
    "author": "JCNS Reverse Engineering Project",
    "version": (0, 10, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > JCNS Editor | File > Import/Export",
    "description": (
        "Import, edit, and export RE Engine JCNS joint constraint files "
        "for Monster Hunter Wilds (v102). Each imported file creates a green "
        "collection with one Empty per constraint, each carrying its full list "
        "of driving sources."
    ),
    "category": "Animation",
}


# ---------------------------------------------------------------------------
# Constants shared across modules
# ---------------------------------------------------------------------------

AXIS_ITEMS = [
    ('X', "X", "骨骼局部 X 轴（或四元数 X 分量）"),
    ('Y', "Y", "骨骼局部 Y 轴（或四元数 Y 分量）"),
    ('Z', "Z", "骨骼局部 Z 轴（或四元数 Z 分量）"),
    ('W', "W", "四元数 W 分量（暂不支持生成驱动器）"),
]

# ConstraintSource_v2 bytes +24 / +25.
#
# These were previously exposed as an "Interpolation" dropdown based on bt v0.65.13,
# which labelled +25 as InterpolationID (Linear / FastInAndOut / …).  Upstream bt
# v0.65.14 renamed +24 to UpdateTimingID and +25 to TransformIDSrc, and the template
# author marks BOTH readings "Not sure".
#
# Survey of 23031 constraint sources across 884 v102 files:
#     +24 ∈ {0: 1700, 1: 3137, 2: 1879, 3: 16306, 4: 2, 5: 7}
#     +25 ∈ {0: 1285, 1: 1455, 2: 373, 3: 19053, 4: 273, 5: 592}
# 21 distinct (+24, +25) combinations occur; the two bytes are NOT locked together.
#
# Neither reading fully accounts for the data: +24 reaches 4 and 5, outside
# UpdateTimingID (0..3), and +25 reaches 5, outside TransformIDSrc (0..4) though
# inside the older InterpolationID (0..6).  Both are marked "Not sure" upstream.
#
# Because the semantics are unresolved and a wrong guess silently changes engine
# behaviour, both are edited as raw bytes rather than named enums.
UPDATE_TIMING_HINT = "0=MotionBegin 1=MotionEnd 2=ConstraintBegin 3=ConstraintEnd (bt 0.65.14 guess)"

TRANSFORM_ITEMS = [
    ('Translation',    "Translation",    "ID=0: Translational constraint"),
    ('Rotation',       "Rotation",       "ID=1: Rotational constraint (most common)"),
    ('Scale',          "Scale",          "ID=2: Scale constraint"),
    ('BlendShape',     "BlendShape",     "ID=3: Blend-shape / morph target"),
    ('UnkCtrl_4',      "UnkCtrl_4",      "ID=4: Unknown control type"),
    ('UnkTopBank_5',   "UnkTopBank_5",   "ID=5: Unknown top-bank type"),
    ('Unknown_6',      "Unknown_6",      "ID=6: Undefined in bt template"),
    ('Material_Color', "Material_Color", "ID=7: Material color drive"),
    ('Material_4D',    "Material_4D",    "ID=8: Material 4D property drive"),
    ('Material_3D',    "Material_3D",    "ID=9: Material 3D property drive"),
    ('Material_2D',    "Material_2D",    "ID=10: Material 2D property drive"),
    ('Scalar',         "Scalar",         "ID=11: Scalar drive"),
    ('Unknown_12',     "Unknown_12",     "ID=12: Unknown"),
    ('UnkRotation_13', "UnkRotation_13", "ID=13: Unknown rotation variant"),
    ('UnkRotation_14', "UnkRotation_14", "ID=14: Unknown rotation variant"),
    ('UnkRotation_15', "UnkRotation_15", "ID=15: Unknown rotation variant"),
    ('UnkRotation_16', "UnkRotation_16", "ID=16: Unknown rotation variant"),
]

AXIS_TO_INT = {'X': 0, 'Y': 1, 'Z': 2, 'W': 3}
INT_TO_AXIS = {0: 'X', 1: 'Y', 2: 'Z', 3: 'W'}

TRANSFORM_TYPE_MAP = {
    0:  'Translation',
    1:  'Rotation',
    2:  'Scale',
    3:  'BlendShape',
    4:  'UnkCtrl_4',
    5:  'UnkTopBank_5',
    6:  'Unknown_6',
    7:  'Material_Color',
    8:  'Material_4D',
    9:  'Material_3D',
    10: 'Material_2D',
    11: 'Scalar',
    12: 'Unknown_12',
    13: 'UnkRotation_13',
    14: 'UnkRotation_14',
    15: 'UnkRotation_15',
    16: 'UnkRotation_16',
}


# ---------------------------------------------------------------------------
# Search callback for source_bone (populated from hash_list at import time)
# ---------------------------------------------------------------------------

def _update_flags_from_bits(self, context):
    """Called when any flag_bit_N changes — repack into cns_flags."""
    self['cns_flags'] = sum(int(getattr(self, f'flag_bit_{i}')) << i for i in range(8))


def _update_bits_from_flags(self, context):
    """Called when cns_flags changes — unpack into flag_bit_N."""
    v = self.cns_flags & 0xFF
    for i in range(8):
        self[f'flag_bit_{i}'] = bool(v & (1 << i))


def _search_bone_names(context, edit_text):
    """Return bone names from available_bones_json that match edit_text (case-insensitive).

    If the typed text is not already in the known list it is prepended as the
    first suggestion so the user can confirm a brand-new bone name by pressing
    Enter or clicking the first item, without being forced onto a partial match.
    """
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
    matches = sorted(n for n in names if needle in n.lower())
    if edit_text and edit_text not in names:
        return [edit_text] + matches
    return matches


def _search_source_bone(self, context, edit_text):
    return _search_bone_names(context, edit_text)


def _search_target_bone(self, context, edit_text):
    return _search_bone_names(context, edit_text)


# ---------------------------------------------------------------------------
# Property Group: one ConstraintSource_v2 block
# ---------------------------------------------------------------------------

class JCNSSourceProperties(PropertyGroup):
    """
    One driving source of a constraint — maps 1:1 onto a 72-byte
    ConstraintSource_v2 block in the file.

    A constraint has SourceCount of these (about 12% of constraints in shipped
    files have more than one; up to 8 have been observed).
    """

    source_bone: StringProperty(
        name="驱动骨骼",
        description="读取旋转的骨骼。可输入以搜索本文件哈希表中的骨骼名",
        default="",
        search=_search_source_bone,
        search_options={'SUGGESTION'},
    )
    source_axis: EnumProperty(
        name="源局部轴向",
        description="读取驱动骨骼的哪个局部轴。JCNS 的映射定义在骨骼自身的局部坐标系上，不是全局坐标系",
        items=AXIS_ITEMS,
        default='X',
    )

    # --- Three-point piecewise mapping ---
    from_start: FloatProperty(
        name="From 起点", description="MapFrom 点A — 第一段起始锚点（源角度，单位度）",
        default=0.0, precision=2, step=10,
    )
    from_kink: FloatProperty(
        name="From 折点", description="MapFrom 点B — 两段斜率的分界折点（源角度，单位度）",
        default=0.0, precision=2, step=10,
    )
    from_end: FloatProperty(
        name="From 终点", description="MapFrom 点C — 第二段终止锚点，源骨骼最大偏转角（单位度）",
        default=0.0, precision=2, step=10,
    )
    to_start: FloatProperty(
        name="To 起点", description="MapTo 点A — 对应 from_start 的输出值",
        default=0.0, precision=2, step=10,
    )
    to_kink: FloatProperty(
        name="To 折点", description="MapTo 点B — 折点处的输出值（引擎读取此值）",
        default=0.0, precision=2, step=10,
    )
    to_end: FloatProperty(
        name="To 终点", description="MapTo 点C — 目标骨骼最大输出旋转量（单位度）",
        default=0.0, precision=2, step=10,
    )

    # --- Rest-pose quaternion ---
    rest_quat_x: FloatProperty(name="Quat X", default=0.0, precision=5)
    rest_quat_y: FloatProperty(name="Quat Y", default=0.0, precision=5)
    rest_quat_z: FloatProperty(name="Quat Z", default=0.0, precision=5)
    rest_quat_w: FloatProperty(name="Quat W", default=1.0, precision=5)

    # --- Raw bytes ---
    update_timing: IntProperty(
        name="更新时机 (+24)",
        description=(
            "ConstraintSource_v2 byte +24. Meaning unconfirmed — bt 0.65.14 reads it as "
            "UpdateTimingID: " + UPDATE_TIMING_HINT + ". Observed values: 0..5"
        ),
        default=3, min=0, max=255,
    )
    src_transform_id: IntProperty(
        name="源变换ID (+25)",
        description=(
            "ConstraintSource_v2 byte +25. Meaning UNCONFIRMED and disputed: bt 0.65.13 read "
            "it as InterpolationID (0..6), bt 0.65.14 reads it as TransformIDSrc (0..4). "
            "Observed values across 884 files are 0..5 — value 5 fits neither reading "
            "cleanly. Change only if you know what you are doing"
        ),
        default=3, min=0, max=255,
    )
    unk_byte2: IntProperty(
        name="未知字节 (+27)", description="ConstraintSource_v2 偏移 +27 的字节",
        default=0, min=0, max=255,
    )
    complex_mapping_info_count: IntProperty(
        name="复杂映射数", description="bt: ComplexMappingInfoCount",
        default=0, min=0, max=65535,
    )
    unknown_uint16: IntProperty(
        name="未知 UInt16", description="ConstraintSource_v2 偏移 +22",
        default=0, min=0, max=65535,
    )
    unknown_uint32_2: IntProperty(
        name="未知 UInt32 (+28)", description="ConstraintSource_v2 偏移 +28",
        default=0, min=0,
    )


# ---------------------------------------------------------------------------
# Property Group: attached to each constraint Empty child object
# ---------------------------------------------------------------------------

class JCNSConstraintProperties(PropertyGroup):
    """
    Stored on every per-constraint child Empty inside a JCNS collection.
    Maps onto one 80-byte ConstraintInfo block plus its list of sources.
    """

    # Explicit marker — set at import / Add Constraint.  Previously the addon
    # detected constraint Empties by 'source_bone is non-empty', which stopped
    # working once sources moved into their own collection.
    is_jcns_constraint: BoolProperty(default=False)

    sources: CollectionProperty(type=JCNSSourceProperties)
    active_source_index: IntProperty(default=0)

    # --- Identity ---
    target_bone: StringProperty(
        name="目标骨骼",
        description="被驱动的骨骼。可输入以搜索本文件哈希表中的骨骼名",
        default="",
        search=_search_target_bone,
        search_options={'SUGGESTION'},
    )
    transform_type: EnumProperty(
        name="变换类型",
        description="约束驱动的是旋转、平移还是缩放等",
        items=TRANSFORM_ITEMS,
        default='Rotation',
    )

    # --- Axis (editable — exported back to file) ---
    target_axis: EnumProperty(
        name="目标局部轴向",
        description="驱动目标骨骼的哪个局部轴。JCNS 的映射定义在骨骼自身的局部坐标系上，不是全局坐标系",
        items=AXIS_ITEMS,
        default='X',
    )

    # --- ConstraintInfo raw fields (editable, exported) ---
    # flags_cns: editable int + 8 bit checkboxes (bidirectional sync via update callbacks)
    cns_flags: IntProperty(
        name="标志位", description="bt: flags_cns。位4（驱动骨骼）与位5（驱动量为旋转）在导出时会按变换类型自动重算；位0 保留你的设置",
        default=0x30, min=0, max=255, update=_update_bits_from_flags,
    )
    flags_expanded: BoolProperty(name="展开标志位", default=False)
    flag_bit_0: BoolProperty(name="Bit0 — isAdd?",  default=False, update=_update_flags_from_bits)
    flag_bit_1: BoolProperty(name="Bit1",            default=False, update=_update_flags_from_bits)
    flag_bit_2: BoolProperty(name="Bit2",            default=False, update=_update_flags_from_bits)
    flag_bit_3: BoolProperty(name="Bit3",            default=False, update=_update_flags_from_bits)
    flag_bit_4: BoolProperty(name="Bit4 — isJoint?",default=True,  update=_update_flags_from_bits)
    flag_bit_5: BoolProperty(name="Bit5",            default=True,  update=_update_flags_from_bits)
    flag_bit_6: BoolProperty(name="Bit6",            default=False, update=_update_flags_from_bits)
    flag_bit_7: BoolProperty(name="Bit7",            default=False, update=_update_flags_from_bits)
    parent_vec4_x: FloatProperty(name="Vec4 X", default=0.0, precision=5)
    parent_vec4_y: FloatProperty(name="Vec4 Y", default=0.0, precision=5)
    parent_vec4_z: FloatProperty(name="Vec4 Z", default=0.0, precision=5)
    parent_vec4_w: FloatProperty(name="Vec4 W", default=1.0, precision=5)
    parent_float2_x: FloatProperty(name="Float2 X", default=0.0, precision=5)
    parent_float2_y: FloatProperty(name="Float2 Y", default=0.0, precision=5)
    parent_uint8_72: IntProperty(
        name="UnknownUInt8 (+72)", description="ConstraintInfo byte at offset +72",
        default=0, min=0, max=255,
    )
    property_hash: IntProperty(
        name="PropertyHash", description="bt: PropertyHash — usually 0",
        default=0, min=0,
    )
    cone_driver_info_count: IntProperty(
        name="ConeDriverInfoCount", description="bt: ConeDriverInfoCount — usually 0",
        default=0, min=0, max=255,
    )
    parent_tail_0: IntProperty(name="Tail[0]", default=0, min=0, max=255)
    parent_tail_1: IntProperty(name="Tail[1]", default=0, min=0, max=255)
    parent_tail_2: IntProperty(name="Tail[2]", default=0, min=0, max=255)
    parent_tail_3: IntProperty(name="Tail[3]", default=0, min=0, max=255)
    parent_tail_4: IntProperty(name="Tail[4]", default=0, min=0, max=255)
    parent_tail_5: IntProperty(name="Tail[5]", default=0, min=0, max=255)

    # --- Material constraint-specific fields (populated at import, editable) ---
    mat_name_hash: StringProperty(
        name="MaterialNameHash",
        description="Direct hash of the material name (hex uint32, e.g. 0x1A2B3C4D)",
        default="0x00000000",
    )
    mat_property_hash: StringProperty(
        name="MaterialPropertyHash",
        description="Direct hash of the material property (hex uint32)",
        default="0x00000000",
    )
    mat_transform_type_raw: IntProperty(
        name="TransformationID",
        description="Material TransformationID byte",
        default=0, min=0, max=255,
    )
    mat_tail_0: IntProperty(name="MatTail[0]", default=0, min=0, max=255)
    mat_tail_1: IntProperty(name="MatTail[1]", default=0, min=0, max=255)
    mat_tail_2: IntProperty(name="MatTail[2]", default=0, min=0, max=255)

    # --- JointExportGraph path (Type 5 empties only) ---
    jxg_path: StringProperty(
        name="路径",
        description="JointExportGraph 路径字符串（文件中为 UTF-16LE）",
        default="",
    )

    # --- Section type (set at import, read-only in UI) ---
    constraint_type: StringProperty(
        name="Constraint Type",
        description="Section type from the JCNS file (e.g. 'Ranges', 'Aim', 'Skin'…)",
        default='Ranges',
    )

    # --- Driver state (runtime, not exported) ---
    driver_applied: BoolProperty(
        name="已应用驱动器",
        description="此约束当前是否已生成 Blender 驱动器",
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
        name="源文件",
        description="原始 .jcns 文件的绝对路径",
        subtype='FILE_PATH',
        default="",
    )
    target_armature: PointerProperty(
        name="目标骨架",
        description="约束要作用到的骨架",
        type=bpy.types.Object,
        poll=_armature_poll,
    )
    available_bones_json: StringProperty(
        name="Available Bones (JSON)",
        description="JSON list of bone names present in this file's hash_list (set at import, used for source_bone autocomplete)",
        default="[]",
    )
    cached_file_header: StringProperty(
        name="Cached File Header",
        description="Base64 of first 0xF0 bytes of source file — allows export without the source file present",
        default="",
    )
    cached_section_table: StringProperty(
        name="Cached Section Table",
        description="Base64 of section table data from source file",
        default="",
    )
    source_combine: EnumProperty(
        name="多源合并",
        description=(
            "How several mapped outputs driving the SAME bone axis are folded into "
            "one value. This covers both a constraint with multiple sources and "
            "several constraints targeting the same channel. The engine's real rule "
            "is NOT yet reverse-engineered — compare against an in-game capture "
            "before trusting any of these"
        ),
        items=[
            ('SUM',     "求和",   "把各路输出相加"),
            ('MAX',     "取最大", "取最大的那一路输出"),
            ('MIN',     "取最小", "取最小的那一路输出"),
            ('AVERAGE', "平均",   "所有输出的平均值"),
            ('FIRST',   "仅第一路", "只用第一个驱动源，忽略其余"),
        ],
        default='SUM',
    )
    detected_game: EnumProperty(
        name="游戏",
        description="Game this JCNS file belongs to (detected at import)",
        items=[
            ('MHW_WILDS', "怪物猎人荒野 (v102)", "Monster Hunter Wilds TU4 之后"),
            ('RE9',       "生化危机9 / PRAGMATA (v35)", "Resident Evil 9 / PRAGMATA"),
        ],
        default='MHW_WILDS',
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
    if props and props.is_jcns_constraint:
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
    def _is_range(obj):
        p = getattr(obj, 'jcns_cns_props', None)
        return p and (p.constraint_type == 'Ranges' or p.constraint_type == '')

    empties = []
    for obj in root_empty.children:
        if _is_range(obj):
            empties.append(obj)

    # Fallback: flat collection search (legacy imports without parent-child hierarchy)
    if not empties:
        for coll in root_empty.users_collection:
            for obj in coll.objects:
                if obj == root_empty:
                    continue
                if _is_range(obj):
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


def channel_key(cns_props):
    """The Blender F-Curve channel a constraint drives: (bone, transform, axis).

    JCNS happily stores several ConstraintInfo blocks that drive the same bone on
    the same axis, and the engine evidently folds them together.  Blender allows
    exactly one driver per F-Curve channel, so constraints sharing a key have to
    be merged into a single driver — applying them one by one just means the last
    one silently replaces all the others.
    """
    return (cns_props.target_bone, cns_props.transform_type, cns_props.target_axis)


def group_constraints_by_channel(root_empty):
    """Return {channel_key: [constraint Empty, …]} preserving constraint order."""
    groups = {}
    for empty in get_constraint_empties(root_empty):
        groups.setdefault(channel_key(empty.jcns_cns_props), []).append(empty)
    return groups


def sibling_constraints(constraint_empty):
    """Other constraint Empties fighting for the same channel as this one."""
    root_obj, _ = get_jcns_root_from_constraint(constraint_empty)
    if root_obj is None:
        return []
    key = channel_key(constraint_empty.jcns_cns_props)
    return [e for e in group_constraints_by_channel(root_obj).get(key, [])
            if e is not constraint_empty]


def make_constraint_empty_name(idx, source_bone, target_bone, target_axis,
                               source_axis='X', extra_sources=0):
    """
    Generate the canonical display name for a constraint Empty.

    extra_sources > 0 appends '(+N)' so multi-source constraints are visible in
    the Outliner without opening the panel.
    """
    src_ax = source_axis if isinstance(source_axis, str) else INT_TO_AXIS.get(source_axis, 'X')
    tgt_ax = target_axis if isinstance(target_axis, str) else INT_TO_AXIS.get(target_axis, 'X')
    suffix = f" (+{extra_sources})" if extra_sources > 0 else ""
    return f"[{idx:02d}] {source_bone or '???'} {src_ax}{suffix} → {target_bone or '???'} {tgt_ax}"


def constraint_name_from_props(idx, props):
    """Build the Empty name straight from a JCNSConstraintProperties instance."""
    srcs = props.sources
    first = srcs[0] if len(srcs) else None
    return make_constraint_empty_name(
        idx,
        first.source_bone if first else '',
        props.target_bone,
        props.target_axis,
        first.source_axis if first else 'X',
        max(0, len(srcs) - 1),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from . import jcns_operators
from . import jcns_importer
from . import jcns_exporter
from . import jcns_ui
from . import jcns_drivers

_classes = [
    JCNSSourceProperties,       # must register before the group that references it
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
