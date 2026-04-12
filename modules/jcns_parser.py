import struct
import os

class JCNSParser:
    """
    Parser for RE Engine JCNS v102 files.

    80-byte ConstraintInfo block layout (parent, at 0xF0 + n*80):
      +0:   ExtraCnsInfo_Offset  uint64   pointer to extra info (0 if none)
      +8:   OffsetSourceList     uint64   pointer to ConstraintSource_v2
      +16:  ObjectNameOffset     uint64   pointer to TARGET bone name (UTF-16LE)
      +24:  PropertyOffset       uint64   pointer to property name (0 usually)
      +32:  TargetHashIndex      uint32   index into hash_list → target bone hash
      +36:  ObjectHash           uint32   direct target bone hash (redundant with above)
      +40:  PropertyHash         uint32   property hash
      +44:  ExtraInfoCount       uint8
      +45:  SourceCount          uint8    number of sources (usually 1)
      +46:  UnknownFlags         uint8    0x30 in all observed files
      +47:  TransformType        uint8    0=Location 1=Rotation 2=Scale 3=BlendShape ...
      +48:  UnknownVector        vec4     [0,0,0,1] (quaternion identity at rest)
      +64:  UnknownFloat2        float[2] [0,0] usually
      +72:  UnknownUInt8         uint8
      +73:  TransformAxis        uint8    AxisID on the parent block (may differ from src-specific tgt axis)
      +74:  UnknownUInt8 × 5

    72-byte ConstraintSource_v2 layout (pointed to by OffsetSourceList):
      +0:   ExtraCnsInfo_Offset  uint64   pointer (0 if none)
      +8:   SourceNameOffset     uint64   pointer to SOURCE bone name (UTF-16LE)
      +16:  SourceHashIndex      uint32   index into hash_list → source bone hash
      +20:  Unknown1             uint32   = 0 in all observed files
      +24:  source_axis          uint8    0=X 1=Y 2=Z 3=W
      +25:  UnkByte1             uint8    = 1 in all observed files
      +26:  target_axis          uint8    0=X 1=Y 2=Z 3=W  (per-source specific)
      +27:  UnkByte2             uint8    = 0 in all observed files
      +28:  UnknownUInt32_2      uint32   = 0
      +32:  from_start           float    Point A source angle (rest-side boundary)
      +36:  from_kink            float    Point B source angle (kink/折点 — slope changes here)
      +40:  from_end             float    Point C source angle (終点 — end of second segment)
      +44:  to_start             float    Point A output (= 0 for one-sided, = extreme for through-range like Back X)
      +48:  to_kink              float    Point B output — engine reads this; 0.0 in all observed files
      +52:  to_end               float    Point C output (= actual maximum target output)
      +56:  rest_quat_x          float    Rest-pose quaternion X — always 0.0
      +60:  rest_quat_y          float    Rest-pose quaternion Y — always 0.0
      +64:  rest_quat_z          float    Rest-pose quaternion Z — always 0.0
      +68:  rest_quat_w          float    Rest-pose quaternion W — always 1.0
    Total: 72 bytes

    JCNS axis convention (AxisID):
      0=X  1=Y  2=Z  3=W (quaternion component)

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
    TRANSFORM_NAMES = {
        0: 'Location', 1: 'Rotation', 2: 'Scale', 3: 'BlendShape',
        4: 'UnkCtrl_4', 5: 'UnkTopBank_5', 7: 'Material_Color',
        8: 'Material_4D', 9: 'Material_3D', 10: 'Material_2D',
        11: 'Scalar',
    }

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
        self._parse_header(data)
        self._parse_hash_list(data)
        self._parse_constraints(data)
        return self.constraints

    def _parse_header(self, data):
        version = struct.unpack_from('<I', data, 0)[0]
        print(f"Version: {version}")
        self.header['Version'] = version

        # TAG struct layout starting at byte 4 (after uint32 version):
        #   [4..7]   magic/signature (4 bytes)
        #   [8..15]  UnknownQWORD
        #   [16..23] InfoOffset (uint64)
        #   [24..31] UnknownQWORD
        #   [32..39] FileEntry (uint64) → address of DataOffset qword
        #   [40..47] UnknownQWORD
        file_entry = struct.unpack_from('<Q', data, 32)[0]
        if file_entry + 8 > len(data):
            raise ValueError(f"FileEntry pointer 0x{file_entry:X} exceeds file size {len(data)}")
        data_offset_raw = struct.unpack_from('<Q', data, file_entry)[0]
        if version == 102:
            data_offset = data_offset_raw * 16
        else:
            data_offset = data_offset_raw
        self.header['DataOffset'] = data_offset

        # Extended header at 0xB0 (v102)
        self.header['UnknownOffset1'] = struct.unpack_from('<Q', data, 0xB0)[0]
        self.header['UnknownOffset2'] = struct.unpack_from('<Q', data, 0xB8)[0]
        self.header['HashListOffset'] = struct.unpack_from('<Q', data, 0xC0)[0]
        self.header['UnknownOffset3'] = struct.unpack_from('<Q', data, 0xC8)[0]

        # Counts at 0xD0
        self.header['HashCount']      = struct.unpack_from('<H', data, 0xD0)[0]
        self.header['UnknownCount1']  = struct.unpack_from('<H', data, 0xD2)[0]
        self.header['ExtraJointCount']= struct.unpack_from('<H', data, 0xD4)[0]
        self.header['ConstraintCount']= struct.unpack_from('<H', data, 0xD6)[0]

        # Entry table at 0xF0 (ConstraintInfo array for v102)
        self.header['ConstraintSetsStart'] = 0xF0
        self.header['ConstraintSetSize']   = 80
        # Store raw entry table bytes for lossless writer
        self.header['EntryTableBlock'] = data[0xF0:0xF0 + 16]
        self.header['CountsBlock']     = data[0xD0:0xD0 + 32]

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
        print(f"DEBUG: Parsing ConstraintSets array at 0xF0, Count = {count}")

        self.constraints = []
        for idx in range(count):
            parent_off = 0xF0 + idx * 80
            parent = data[parent_off:parent_off + 80]

            c = {}
            # --- Parent block fields ---
            c['ParentSetOffset']    = parent_off
            c['ExtraCnsInfo_Offset']= struct.unpack_from('<Q', parent, 0)[0]
            src_list_ptr            = struct.unpack_from('<Q', parent, 8)[0]
            c['LimitsPointer']      = src_list_ptr
            obj_name_off            = struct.unpack_from('<Q', parent, 16)[0]
            c['PropertyOffset']     = struct.unpack_from('<Q', parent, 24)[0]
            c['TargetHashIndex']    = struct.unpack_from('<I', parent, 32)[0]  # index into hash_list
            c['ObjectHash']         = struct.unpack_from('<I', parent, 36)[0]  # direct hash value
            c['PropertyHash']       = struct.unpack_from('<I', parent, 40)[0]
            c['ExtraInfoCount_parent'] = parent[44]
            c['SourceCount_parent']    = parent[45]
            c['UnknownFlags']          = parent[46]
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
            if 0 <= c['TargetHashIndex'] < len(self.hash_list):
                c['TargetHash'] = self.hash_list[c['TargetHashIndex']]
            else:
                c['TargetHash'] = c['ObjectHash']

            # --- 72-byte ConstraintSource_v2 ---
            src = self._parse_source_struct(data, src_list_ptr)
            c.update(src)

            self.constraints.append(c)

        # Print summary
        for i, c in enumerate(self.constraints):
            sa = self.AXIS_NAMES[min(c.get('source_axis', 0), 3)]
            ta = self.AXIS_NAMES[min(c.get('target_axis', 0), 3)]
            print(
                f"[{i:02d}] source={c.get('SourceName','?'):<14} "
                f"targetHashIdx={c['TargetHashIndex']}  "
                f"targetHash32=0x{c['TargetHash']:08x}  "
                f"srcAxis={sa}({c.get('source_axis',0)})  "
                f"tgtAxis={ta}({c.get('target_axis',0)})  "
                f"From=[{c['from_start']}, kink={c['from_kink']}, {c['from_end']}] "
                f"To=[{c['to_start']}, kink={c['to_kink']}, {c['to_end']}]"
            )

    def _parse_source_struct(self, data, ptr):
        """Parse the 72-byte ConstraintSource_v2 starting at ptr."""
        s = {}
        if ptr == 0 or ptr + 72 > len(data):
            s['SourceName'] = ''
            s['SourceHashIndex'] = 0
            s['source_axis'] = 0
            s['target_axis'] = 0
            return s

        block = data[ptr:ptr + 72]
        s['ExtraCnsInfo_Offset'] = struct.unpack_from('<Q', block, 0)[0]
        name_off                  = struct.unpack_from('<Q', block, 8)[0]
        s['SourceName_Offset']    = name_off
        s['SourceHashIndex']      = struct.unpack_from('<I', block, 16)[0]
        s['Unknown1']             = struct.unpack_from('<I', block, 20)[0]

        # Axis bytes packed at +24
        s['source_axis'] = block[24]   # AxisID: 0=X 1=Y 2=Z 3=W
        s['UnkByte1']    = block[25]   # typically 1
        s['target_axis'] = block[26]   # AxisID: 0=X 1=Y 2=Z 3=W
        s['UnkByte2']    = block[27]   # typically 0

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
