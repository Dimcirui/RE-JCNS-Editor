import struct
import os
import sys

# Version-specific layout constants.  DataEntry pointer offsets (0x50–0xC0) are
# identical for all supported versions; only the counts region and ConstraintInfo
# start differ.
LAYOUTS = {
    102: {
        'counts_base': 0xD0,
        'cns_info_start': 0xF0,   # fixed; None → read ConstraintInfoEntry @ 0x58
        'counts_fields': {
            'HashCount':                       (0x00, '<i'),
            'ConeDriverCount':                 (0x04, '<H'),
            'ConstraintCount':                 (0x06, '<H'),
            'DependencyCount':                 (0x08, '<H'),
            'ObjectSettingCount':              (0x0A, '<H'),
            'RotExpressionInfoCount':          (0x0C, '<H'),
            'RotExpressionMapCount':           (0x0E, '<H'),
            'SkinConstraintCount':             (0x10, '<H'),
            'SkinConstraintHashTableItemCount':(0x12, '<H'),
            'SkinConstraintSourceCount':       (0x14, '<H'),
            'AimConstraintCount':              (0x16, '<H'),
            'MaterialConstraintInfoCount':     (0x18, '<H'),
            'SectionCount':                    (0x1A, '<B'),
        },
    },
    35: {
        'counts_base': 0xC8,
        'cns_info_start': None,   # read ConstraintInfoEntry @ 0x58
        'counts_fields': {
            'HashCount':                  (0x00, '<i'),
            'ConeDriverCount':            (0x04, '<H'),
            'ConstraintCount':            (0x06, '<H'),
            'DependencyCount':            (0x08, '<H'),
            'ObjectSettingCount':         (0x0A, '<H'),
            'RotExpressionInfoCount':     (0x0C, '<H'),
            'RotExpressionMapCount':      (0x0E, '<H'),
            'SkinConstraintCount':        (0x10, '<H'),
            'SkinConstraintSourceCount':  (0x12, '<H'),
            'AimConstraintCount':         (0x14, '<H'),
            'MaterialConstraintInfoCount':(0x16, '<H'),
            # 0x18: UnknownUInt16 — skipped
            'SectionCount':               (0x1A, '<B'),
        },
    },
}

VERSION_GAME_MAP = {102: 'MHW_WILDS', 35: 'RE9'}


def _hash_utf16(name):
    """MurmurHash3 of a UTF-16LE bone name, as RE Engine computes it."""
    hashing_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hashing')
    if hashing_dir not in sys.path:
        sys.path.insert(0, hashing_dir)
    from mmh3.pymmh3 import hashUTF16
    return hashUTF16(name) & 0xFFFFFFFF


class JCNSParser:
    """
    Parser for RE Engine JCNS v102 files.

    80-byte ConstraintInfo block layout (parent, at 0xF0 + n*80):
      +0:   ConeDriverInfoOffset  uint64   bt: ConeDriverInfoList.Offset (0=none)
      +8:   OffsetSourceList      uint64   pointer to ConstraintSource_v2
      +16:  ObjectNameOffset      uint64   pointer to TARGET bone name (UTF-16LE)
      +24:  PropertyOffset        uint64   pointer to property name (0 usually)
      +32:  TargetHashIndex       uint32   index into hash_list → target bone hash
      +36:  ObjectHash            uint32   direct target bone hash (redundant with above)
      +40:  PropertyHash          uint32   property hash
      +44:  ConeDriverInfoCount   uint8    bt: ConeDriverInfoCount — 0 in every one of the
                                             19884 constraints surveyed; no shipped file uses
                                             ConeDrivers at all.
      +45:  SourceCount           uint8    number of ConstraintSource_v2 blocks; ~12% of
                                             constraints have more than 1 (up to 8 observed)
      +46:  Flags                 uint8    bt: flags_cns.  11 distinct values observed; bit4/bit5
                                             are deterministic functions of TransformType
                                             (bit4 "isJoint" set for types {0,1,2,4,5,6,13,14},
                                             bit5 "isAngular" for the rotation-ish subset
                                             {1,4,5,6,13,14}).  bit0 ("isAdd?" per bt) is the only
                                             bit that varies independently within one
                                             TransformType — see modules/jcns_flags.py.
                                             bits 1/6/7 never set in any observed file.
      +47:  TransformType         uint8    bt: TransformationID  0=Translation 1=Rotation 2=Scale …
      +48:  UnknownVector4D       vec4     [0,0,0,1] in every observed constraint
      +64:  UnknownFloat2         float[2] [0,0] in 99.6%; the rest look like angle limits
                                             (e.g. [-45,0], [-90,-90], [-20,-20])
      +72:  UnknownUInt8          uint8    0 in 98.5%; also seen {1,2,3,4}
      +73:  TransformAxis         uint8    bt: AxisID — target axis (may differ from src-specific).
                                             Unlike source_axis, this DOES take W (1.3%).
      +74:  UnknownUInt8 × 6      Not six free bytes: +76/+78/+79 are always 0 and +74 is 0 in
                                             98.8%, but +75 and +77 are two live enum-ish fields.
                                             +75 ∈ {0,1,2,3,4,5,8} (2 dominates at 70%),
                                             +77 ∈ {0,1,2,3,4} (0 dominates at 74%).
                                             Meaning unknown; preserved verbatim on write.

    72-byte ConstraintSource_v2 layout (pointed to by OffsetSourceList):
      +0:   ComplexMappingInfoOffset  uint64   bt: ComplexMappingInfoOffset (0=none)
      +8:   SourceNameOffset          uint64   pointer to SOURCE bone name (UTF-16LE)
      +16:  SourceHashIndex           uint32   index into hash_list → source bone hash
      +20:  ComplexMappingInfoCount   uint16   bt: ComplexMappingInfoCount.  Nonzero in 78 of
                                              23031 sources, taking values {3, 4, 7}.
      +22:  UnknownUInt16             uint16   0 in all but a single observed source (which has 1).
      +24:  UpdateTiming              uint8    bt(0.65.14): UpdateTimingID.  All six enum values
                                              occur: MotionBegin(7.4%) MotionEnd(13.6%)
                                              ConstraintBegin(8.2%) ConstraintEnd(70.8%)
                                              Last(2 sources) ByBehavior(7 sources).
                                              Appears to encode evaluation order — sources that are
                                              themselves constraint outputs tend to read at
                                              ConstraintEnd, raw animated bones earlier — rather
                                              than anything about how sources combine.
      +25:  SrcTransformID            uint8    MEANING UNCERTAIN — bt 0.65.13 called this
                                              InterpolationID, bt 0.65.14 renamed it to
                                              TransformIDSrc; the template author marks both
                                              "Not sure".  Observed {0,1,2,3,4,5}: mostly
                                              Src_Rotation_3 (82.7%), but value 5 is beyond the
                                              bt enum's last defined entry.  Among real bone
                                              rotation targets, the 70 sources tagged
                                              Src_Translation(0) carry From ranges shaped like the
                                              rotation ones (91% land on multiples of 5, same
                                              magnitude band), so the tag alone does not establish
                                              a distance-driven mechanism.  Treated as a raw byte.
      +26:  source_axis               uint8    bt: SourceAxis  0=X 1=Y 2=Z 3=W.  Only {X,Y,Z} ever
                                              observed here — sources never use W, though targets do.
      +27:  UnkByte2                  uint8    NOT constant: {0:79.3%, 1:14.8%, 2:5.6%, 3:0.3%}.
      +28:  UnknownUInt32_2      uint32   Really two live bytes; +30/+31 are always 0.
                                              +28 ∈ {0,1,2,3} (0 in 91.5%).
                                              +29 == 1 iff ComplexMappingInfoCount > 0 (exact
                                              match across all 78 cases), so it reads as that
                                              feature's enable flag; +29 == 2 occurs in 39 further
                                              sources with no complex mapping and is unexplained.
      +32:  from_start           float    Point A source angle (rest-side boundary)
      +36:  from_kink            float    Point B source angle (kink/折点 — slope changes here)
      +40:  from_end             float    Point C source angle (終点 — end of second segment)
      +44:  to_start             float    Point A output (= 0 for one-sided, = extreme for through-range like Back X)
      +48:  to_kink              float    Point B output — engine reads this.  NOT a dead field:
                                        nonzero in 14.8% of sources across 98 distinct values,
                                        so the mapping's middle anchor is genuinely used.
      +52:  to_end               float    Point C output (= actual maximum target output)
      +56:  rest_quat_x          float    Rest-pose quaternion X — 0.0 in every observed source
      +60:  rest_quat_y          float    Rest-pose quaternion Y — 0.0 except 2 sources (-0.7071)
      +64:  rest_quat_z          float    Rest-pose quaternion Z — 0.0 in every observed source
      +68:  rest_quat_w          float    Rest-pose quaternion W — 1.0 except the same 2 sources
                                        (0.7071); together those two encode a 90° rotation about Y
                                        rather than identity, so this is a real rest pose, not padding.
    Total: 72 bytes

    Field-frequency claims above were measured over 884 shipped .jcns.102 files
    (19884 constraints / 23031 sources).  Every non-constant field listed here is
    round-tripped verbatim by jcns_writer; the defaults it falls back to apply only to
    newly created constraints.

    JCNS axis convention (AxisID):
      0=X  1=Y  2=Z  3=W (quaternion component)
      bt 0.65.14 also defines UnknownAxis_4..8, none of which occur in the corpus.

    Mapping formula (correct 2-anchor interpretation):
      output = clamp(
          to_max + (source - from_max) * (to_min - to_max) / (from_min - from_max),
          min(to_min, to_max),
          max(to_min, to_max)
      )
      At source=from_max → output=to_max (anchor A)
      At source=from_min → output=to_min (anchor B)
      At source outside range → clamped to boundary → 0 at rest pose ✓
    """

    AXIS_NAMES = ['X', 'Y', 'Z', 'W']
    # No transform-type name table here on purpose: TRANSFORM_TYPE_MAP in __init__.py
    # is the single source of truth, and it covers all of bt 0.65.14's IDs 0-16.  A
    # second copy living down here went stale without anyone noticing (it stopped at
    # 11, missing Unknown_6 and UnkRotation_13/14 — between them 19% of every
    # constraint in the shipped corpus).

    def __init__(self, filepath):
        self.filepath = filepath
        self.header = {}
        self.hash_list = []
        self.string_pool = {}
        self.constraints = []

    def _read_wstring(self, data, offset):
        """Read a null-terminated UTF-16LE string from data at offset."""
        if offset == 0 or offset >= len(data):
            return ''
        chars = []
        pos = offset
        while pos + 1 < len(data):
            ch = data[pos:pos+2]
            if ch == b'\x00\x00':
                break
            chars.append(ch)
            pos += 2
        try:
            return b''.join(chars).decode('utf-16le')
        except Exception:
            return ''

    def parse(self):
        with open(self.filepath, 'rb') as f:
            data = f.read()
        self.original_bytes = data
        self.aim_constraints    = []
        self.rot_expressions    = []
        self.rot_expression_map = b''
        self.material_cns       = []
        self.joint_export_graph = None
        self._parse_header(data)
        self._parse_hash_list(data)
        self._parse_constraints(data)
        self._store_inline_blob(data)
        self._parse_aim_constraints(data)
        self._parse_rot_expressions(data)
        self._parse_material_cns(data)
        self._parse_joint_export_graph(data)
        return self.constraints

    def _parse_header(self, data):
        version = struct.unpack_from('<I', data, 0)[0]
        print(f"Version: {version}")
        self.header['Version'] = version

        if version not in LAYOUTS:
            raise ValueError(f"Unsupported JCNS version: {version}. Supported: {list(LAYOUTS)}")
        layout = LAYOUTS[version]
        self.header['layout'] = layout

        # Tags block: JCNSEntry at byte 32 → DataEntry pointer
        file_entry = struct.unpack_from('<Q', data, 32)[0]
        if file_entry + 8 > len(data):
            raise ValueError(f"FileEntry pointer 0x{file_entry:X} exceeds file size {len(data)}")
        data_entry = struct.unpack_from('<Q', data, file_entry)[0]
        self.header['DataEntry'] = data_entry

        # DataEntry pointer region (0x50–0xC0) — identical layout for all supported versions
        self.header['SectionTableEntry']       = struct.unpack_from('<Q', data, 0xB0)[0]
        self.header['DependencyTableEntry']    = struct.unpack_from('<Q', data, 0xB8)[0]
        self.header['HashListOffset']          = struct.unpack_from('<Q', data, 0xC0)[0]
        self.header['AimConstraintTableEntry'] = struct.unpack_from('<Q', data, 0x98)[0]

        # Counts region — version-specific base offset and field layout
        cb = layout['counts_base']
        for field, (off, fmt) in layout['counts_fields'].items():
            self.header[field] = struct.unpack_from(fmt, data, cb + off)[0]

        # ConstraintInfo start: fixed value or read from ConstraintInfoEntry pointer @ 0x58
        if layout['cns_info_start'] is not None:
            self.header['ConstraintSetsStart'] = layout['cns_info_start']
        else:
            self.header['ConstraintSetsStart'] = struct.unpack_from('<Q', data, 0x58)[0]
        self.header['ConstraintSetSize'] = 80

    def _parse_rot_expressions(self, data):
        """
        Parse BODY1: RotExpression section (Type 1).

        For v102 (>=35), four sub-sections:
          RotExpressionInfo[N]            at 0x68,  56 bytes each (direct hashes)
          RotExpressionMap[M]             at 0x70,  1 byte each
          SourceJointHashIndices[N]       at 0x78,  4 bytes each (int32 index)
          JointHashIndices[N]             at 0x80,  4 bytes each (int32 index)
        Counts at 0xDC (InfoCount) and 0xDE (MapCount).
        """
        n = self.header.get('RotExpressionInfoCount', 0)
        self.rot_expressions = []
        if n == 0:
            return

        info_off   = struct.unpack_from('<Q', data, 0x68)[0]
        map_off    = struct.unpack_from('<Q', data, 0x70)[0]
        src_idx_off= struct.unpack_from('<Q', data, 0x78)[0]
        jnt_idx_off= struct.unpack_from('<Q', data, 0x80)[0]
        m          = struct.unpack_from('<H', data, 0xDE)[0]

        for i in range(n):
            base = info_off + i * 56
            block = data[base:base + 56]
            # RotExpressionInfo layout: vec4 Rotation[0..15], vec4 Scale[16..31],
            # hash JointHash[32..35], hash SourceJointHash[36..39], uint8[4][40..43], float[3][44..55]
            joint_hash  = struct.unpack_from('<I', block, 32)[0]
            src_hash    = struct.unpack_from('<I', block, 36)[0]

            # Hash-index arrays (int32 each; -1 = unused)
            src_idx = struct.unpack_from('<i', data, src_idx_off + i * 4)[0] if src_idx_off else -1
            jnt_idx = struct.unpack_from('<i', data, jnt_idx_off + i * 4)[0] if jnt_idx_off else -1

            self.rot_expressions.append({
                'JointHash':      joint_hash,
                'SourceJointHash': src_hash,
                'SrcJointHashIndex': src_idx,
                'JntHashIndex':   jnt_idx,
                'info_raw':  bytes(block),          # 56 bytes — direct hashes, copy verbatim
            })

        self.rot_expression_map = bytes(data[map_off:map_off + m]) if map_off and m else b''
        print(f"Parsed {n} RotExpression(s), map={m}")

    def _parse_material_cns(self, data):
        """
        Parse BODY4: Material constraint section (Type 4).

        For v102 (>=35), each MatCnsInfo is 16 bytes:
          [0]  int32  JointHashIndex   → hash_list[idx]
          [4]  uint32 MaterialNameHash (direct)
          [8]  uint32 MaterialPropertyHash (direct)
          [12] uint8  TransformationID
          [13..15] 3 unknown bytes
        """
        n   = struct.unpack_from('<H', data, 0xE8)[0]
        off = struct.unpack_from('<Q', data, 0xA0)[0]
        self.material_cns = []
        if n == 0 or off == 0:
            return

        for i in range(n):
            base = off + i * 16
            block = data[base:base + 16]
            jh_idx = struct.unpack_from('<i', block, 0)[0]
            jh     = self.hash_list[jh_idx] if 0 <= jh_idx < len(self.hash_list) else 0
            self.material_cns.append({
                'JointHashIndex': jh_idx,
                'JointHash':      jh,
                'raw_body': bytes(block[4:]),  # 12 bytes after JointHashIndex (recomputed on write)
            })
        print(f"Parsed {n} MaterialConstraint(s)")

    def _parse_joint_export_graph(self, data):
        """
        Parse BODY5: JointExportGraph (Type 5) — zero or one entry.

        Structure:
          uint64 PathOffset  → pointer to UTF-16LE null-terminated path string
        """
        off = struct.unpack_from('<Q', data, 0xA8)[0]
        self.joint_export_graph = None
        if off == 0:
            return

        path_ptr = struct.unpack_from('<Q', data, off)[0]
        path_str = self._read_wstring(data, path_ptr) if path_ptr else ''
        self.joint_export_graph = {'path': path_str}
        print(f"Parsed JointExportGraph: '{path_str}'")

    def _parse_aim_constraints(self, data):
        """
        Parse ConstraintAim entries (BODY3, Section Type 3).

        For v102 (>= 35 in bt terms) each inline block is 80 bytes:
          [0]   uint64  AimTargetInfo.Offset  → pointer to 16-byte target block
          [8]   int32   JointHashIndex        → hash_list[idx] = joint being aimed
          [12]  int32   UnkJointHashIndex     → hash_list[idx] or -1 (unused)
          [16..79]      vectors + tail bytes (72 bytes - 8 = actually bytes[16..79], 64b)
        Wait: [8..79] is 72 bytes total inline body after offset.

        Target block (16 bytes at Offset):
          [0]   int32   TargetJointHashIndex  → hash_list[idx] = target bone
          [4]   float   Influence
          [8]   uint64  UnknownQWORD
        """
        count  = self.header.get('AimConstraintCount', 0)
        offset = self.header.get('AimConstraintTableEntry', 0)
        self.aim_constraints = []
        if count == 0 or offset == 0:
            return

        for i in range(count):
            base  = offset + i * 80
            block = data[base:base + 80]

            tgt_ptr          = struct.unpack_from('<Q', block, 0)[0]
            joint_idx        = struct.unpack_from('<i', block, 8)[0]   # signed: -1 = unused
            unk_joint_idx    = struct.unpack_from('<i', block, 12)[0]

            joint_hash     = self.hash_list[joint_idx]     if 0 <= joint_idx     < len(self.hash_list) else 0
            unk_joint_hash = self.hash_list[unk_joint_idx] if 0 <= unk_joint_idx < len(self.hash_list) else 0

            # Parse target block
            tgt_idx   = -1
            tgt_hash  = 0
            if 0 < tgt_ptr and tgt_ptr + 16 <= len(data):
                tgt_idx  = struct.unpack_from('<i', data, tgt_ptr)[0]
                tgt_hash = self.hash_list[tgt_idx] if 0 <= tgt_idx < len(self.hash_list) else 0
                tgt_body = bytes(data[tgt_ptr + 4 : tgt_ptr + 16])  # Influence + UnkQW (12 bytes)
            else:
                tgt_body = b'\x00' * 12

            self.aim_constraints.append({
                'JointHashIndex':    joint_idx,
                'JointHash':         joint_hash,
                'UnkJointHashIndex': unk_joint_idx,
                'UnkJointHash':      unk_joint_hash,
                'TargetHashIndex':   tgt_idx,
                'TargetHash':        tgt_hash,
                # Bytes [8..79] of inline block (excludes the 8-byte offset pointer)
                'inline_body':  bytes(block[8:]),   # 72 bytes
                # Bytes [4..15] of target block (excludes the 4-byte hash-index)
                'target_body':  tgt_body,            # 12 bytes
            })

        print(f"Parsed {count} Aim constraint(s):")
        for i, ac in enumerate(self.aim_constraints):
            print(f"  Aim[{i}]: joint=0x{ac['JointHash']:08X}  target=0x{ac['TargetHash']:08X}")

    def _parse_hash_list(self, data):
        hl_offset = self.header['HashListOffset']
        count = self.header['HashCount']
        self.hash_list = []
        if count > 0 and hl_offset > 0:
            for i in range(count):
                h = struct.unpack_from('<I', data, hl_offset + i * 4)[0]
                self.hash_list.append(h)
        print(f"Hash list ({len(self.hash_list)} entries):")
        for i, h in enumerate(self.hash_list):
            print(f"  [{i}] 0x{h:08x}")

    def _parse_constraints(self, data):
        count = self.header['ConstraintCount']
        if count == 0:
            return
        print(f"DEBUG: Parsing ConstraintSets array at 0x{self.header['ConstraintSetsStart']:X}, Count = {count}")

        self.constraints = []
        for idx in range(count):
            parent_off = self.header['ConstraintSetsStart'] + idx * 80
            parent = data[parent_off:parent_off + 80]

            c = {}
            # --- Parent block fields ---
            c['ParentSetOffset']    = parent_off
            c['ConeDriverInfoOffset']= struct.unpack_from('<Q', parent, 0)[0]
            src_list_ptr            = struct.unpack_from('<Q', parent, 8)[0]
            c['LimitsPointer']      = src_list_ptr
            obj_name_off            = struct.unpack_from('<Q', parent, 16)[0]
            c['PropertyOffset']     = struct.unpack_from('<Q', parent, 24)[0]
            c['TargetHashIndex']    = struct.unpack_from('<I', parent, 32)[0]  # index into hash_list
            c['ObjectHash']         = struct.unpack_from('<I', parent, 36)[0]  # direct hash value
            c['PropertyHash']       = struct.unpack_from('<I', parent, 40)[0]
            c['ConeDriverInfoCount']   = parent[44]
            c['SourceCount_parent']    = parent[45]
            c['Flags']                 = parent[46]
            c['TransformType']         = parent[47]
            # vec4 at +48
            c['ParentVec4'] = struct.unpack_from('<4f', parent, 48)
            # float[2] at +64
            c['ParentFloat2'] = struct.unpack_from('<2f', parent, 64)
            c['ParentUInt8_72'] = parent[72]
            c['TransformAxis_parent'] = parent[73]  # parent-level axis (may differ from per-source)
            c['ParentTailBytes'] = bytes(parent[74:80])

            # Resolve target bone name
            c['TargetBoneName'] = self._read_wstring(data, obj_name_off)
            c['TargetBoneNameOffset'] = obj_name_off

            # Does ObjectHash actually correspond to ObjectName?
            #
            # For ordinary bones it does.  But ObjectName can also be an RSZ object
            # or property target (e.g. 'via.motion.Chain' with TransformType=11 and a
            # non-zero PropertyHash), where ObjectHash is derived from something else
            # entirely.  Recomputing the hash from the name in those cases silently
            # rewrites it to a wrong value, so record the answer while both fields
            # are still original and let the writer decide.
            c['ObjectHashMatchesName'] = bool(
                c['TargetBoneName']
                and (_hash_utf16(c['TargetBoneName']) == c['ObjectHash'])
            )
            if 0 <= c['TargetHashIndex'] < len(self.hash_list):
                c['TargetHash'] = self.hash_list[c['TargetHashIndex']]
            else:
                c['TargetHash'] = c['ObjectHash']

            # --- ConstraintSource_v2 array: SourceCount consecutive 72-byte blocks ---
            # bt: ConstraintSourceList reads CSource[SourceCount] at OffsetSourceList.
            # Multi-source constraints are common (≈12% of all constraints observed),
            # e.g. C_Spine_MB_XYZ_HJ driven by Spine_0 + Spine_1 + Spine_2.
            c['sources'] = []
            if src_list_ptr:
                for k in range(c['SourceCount_parent']):
                    c['sources'].append(
                        self._parse_source_struct(data, src_list_ptr + k * 72)
                    )

            # target_axis = TransformAxis from ConstraintInfo[+73], not from source block
            c['target_axis'] = c['TransformAxis_parent']

            self.constraints.append(c)

        # Print summary
        for i, c in enumerate(self.constraints):
            ta = self.AXIS_NAMES[min(c.get('target_axis', 0), 3)]
            head = (f"[{i:02d}] target={c.get('TargetBoneName','?'):<20} "
                    f"tgtAxis={ta}  hashIdx={c['TargetHashIndex']}  "
                    f"hash32=0x{c['TargetHash']:08x}")
            if not c['sources']:
                print(head + "  (no sources)")
                continue
            print(f"{head}  << {len(c['sources'])} source(s)")
            for k, s in enumerate(c['sources']):
                sa = self.AXIS_NAMES[min(s.get('source_axis', 0), 3)]
                print(
                    f"       src[{k}] {s.get('SourceName','?'):<16} axis={sa}  "
                    f"From=[{s.get('from_start')}, kink={s.get('from_kink')}, {s.get('from_end')}] "
                    f"To=[{s.get('to_start')}, kink={s.get('to_kink')}, {s.get('to_end')}]"
                )

    def _store_inline_blob(self, data):
        """
        For v35 files only: capture the region between ConstraintInfo-end and DependencyTable-start.
        This region contains ConeDriverInfo blobs, Source_v2 structs with embedded WStrings,
        and unknown gap data.  The writer preserves it verbatim, patching only editable fields.
        v102 files always use the full rebuild path and must never set inline_blob.
        """
        if self.header.get('Version') != 35:
            self.inline_blob = b''
            return
        cns_end = self.header['ConstraintSetsStart'] + self.header['ConstraintCount'] * 80
        dep_start = self.header.get('DependencyTableEntry', 0)
        self.inline_blob_cns_end = cns_end
        if dep_start > cns_end:
            self.inline_blob = bytes(data[cns_end:dep_start])
            print(f"[JCNS] inline_blob: 0x{cns_end:X}–0x{dep_start:X} ({len(self.inline_blob)} bytes)")
        else:
            self.inline_blob = b''

    def _parse_source_struct(self, data, ptr):
        """Parse the 72-byte ConstraintSource_v2 starting at ptr."""
        if ptr == 0 or ptr + 72 > len(data):
            # Fully-populated defaults so downstream code never sees a missing key.
            return {
                'SourceName': '', 'SourceName_Offset': 0, 'SourceHashIndex': 0,
                'source_axis': 0, 'ComplexMappingInfoOffset': 0,
                'ComplexMappingInfoCount': 0, 'UnknownUInt16': 0,
                'UpdateTiming': 3, 'SrcTransformID': 3, 'UnkByte2': 0,
                'UnknownUInt32_2': 0,
                'from_start': 0.0, 'from_kink': 0.0, 'from_end': 0.0,
                'to_start': 0.0, 'to_kink': 0.0, 'to_end': 0.0,
                'rest_quat_x': 0.0, 'rest_quat_y': 0.0,
                'rest_quat_z': 0.0, 'rest_quat_w': 1.0,
            }
        s = {}

        block = data[ptr:ptr + 72]
        s['ComplexMappingInfoOffset'] = struct.unpack_from('<Q', block, 0)[0]
        name_off                      = struct.unpack_from('<Q', block, 8)[0]
        s['SourceName_Offset']        = name_off
        s['SourceHashIndex']          = struct.unpack_from('<I', block, 16)[0]
        s['ComplexMappingInfoCount']  = struct.unpack_from('<H', block, 20)[0]
        s['UnknownUInt16']            = struct.unpack_from('<H', block, 22)[0]

        # +24: UpdateTimingID (observed 0..3); +25: meaning uncertain, see class docstring
        # +26: SourceAxis (0=X 1=Y 2=Z 3=W); +27: unknown (always 0)
        s['UpdateTiming']   = block[24]
        s['SrcTransformID'] = block[25]
        s['source_axis'] = block[26]
        s['UnkByte2']    = block[27]

        s['UnknownUInt32_2'] = struct.unpack_from('<I', block, 28)[0]

        # Three-point piecewise linear mapping: (from_start,to_start) → (from_kink,to_kink) → (from_end,to_end)
        s['from_start']     = struct.unpack_from('<f', block, 32)[0]
        s['from_kink']      = struct.unpack_from('<f', block, 36)[0]
        s['from_end']       = struct.unpack_from('<f', block, 40)[0]
        s['to_start']       = struct.unpack_from('<f', block, 44)[0]
        s['to_kink']        = struct.unpack_from('<f', block, 48)[0]  # 0.0 in all observed files
        s['to_end']         = struct.unpack_from('<f', block, 52)[0]

        # Rest-pose quaternion [X, Y, Z, W] — always [0, 0, 0, 1] in all observed files
        s['rest_quat_x'] = struct.unpack_from('<f', block, 56)[0]
        s['rest_quat_y'] = struct.unpack_from('<f', block, 60)[0]
        s['rest_quat_z'] = struct.unpack_from('<f', block, 64)[0]
        s['rest_quat_w'] = struct.unpack_from('<f', block, 68)[0]

        s['SourceName'] = self._read_wstring(data, name_off)

        # Source hash for reference
        if 0 <= s['SourceHashIndex'] < 256:  # sanity guard
            pass  # caller can look up hash_list if needed

        return s

    # Legacy compatibility shim — old code used parse_constraint_info() and
    # _parse_constraint_flattened(); keep for writer which still calls the old API.
    def parse_constraint_info(self, f):
        raise NotImplementedError("Use parse() instead — new parser reads from bytes directly")


if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sample = os.path.abspath(os.path.join(script_dir, '..', '..', 'binaries', 'ch03_012_0012.jcns.102'))
    p = JCNSParser(sample)
    cns = p.parse()
    print(f"\nTotal: {len(cns)} constraints")
