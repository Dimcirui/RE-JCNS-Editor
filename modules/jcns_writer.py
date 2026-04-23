import struct
import os
import sys
from jcns_parser import LAYOUTS


class JCNSWriter:
    def __init__(self, parser_obj, filepath):
        self.parser = parser_obj
        self.filepath = filepath

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_lossless(self, clean_hashes=False):
        """
        Fully rebuild the JCNS v102 file, supporting:
          - Changed source/target bone names (with hash lookup)
          - Changed mapping values, axes, rest quaternion
          - Added or deleted constraint blocks
        All sections are rebuilt from scratch; only the 80-byte Tags block
        (0x00..0x4F) and the static per-constraint fields are preserved
        from the original.
        """
        return self._build_full(clean_hashes)

    # ------------------------------------------------------------------
    # Internal rebuild
    # ------------------------------------------------------------------

    def _build_full(self, clean_hashes=False):
        p = self.parser
        orig = p.original_bytes

        # ── locate hashing module ──────────────────────────────────────
        hash_dir = os.path.join(os.path.dirname(__file__), 'hashing')
        if hash_dir not in sys.path:
            sys.path.insert(0, hash_dir)
        from mmh3.pymmh3 import hashUTF16  # noqa: F401 (runtime import)

        # ── Phase 1: build new hash list ────────────────────────────────
        if clean_hashes:
            new_hash_list = []
        else:
            new_hash_list = list(p.hash_list)

        def _get_or_add(name):
            """Return (hash32, index) for a bone name, adding to list if missing."""
            if not name:
                return 0, 0
            h = hashUTF16(name) & 0xFFFFFFFF
            for i, v in enumerate(new_hash_list):
                if v == h:
                    return h, i
            idx = len(new_hash_list)
            new_hash_list.append(h)
            return h, idx

        def _get_or_add_hash(h):
            """Return index for a raw hash value (used for non-Range sections)."""
            for i, v in enumerate(new_hash_list):
                if v == h:
                    return i
            idx = len(new_hash_list)
            new_hash_list.append(h)
            return idx

        def _is_direct_hash(c):
            """True when target is identified by direct ObjectHash (TgtIdx=0xFFFFFFFF),
            e.g. BlendShape / property targets that don't live in the hash table."""
            return c.get('TargetHashIndex', 0) == 0xFFFFFFFF

        # Rebuild hashes for all source and target bones (Range constraints)
        for c in p.constraints:
            src = c.get('SourceName', '')
            if src:
                src_h, src_idx = _get_or_add(src)
                c['SourceHashIndex'] = src_idx

            # Only bone targets go into the hash table; BlendShape/property targets
            # use direct ObjectHash (TgtIdx=0xFFFFFFFF) and must not pollute the list.
            tgt = c.get('TargetBoneName', '')
            if tgt and not _is_direct_hash(c):
                _get_or_add(tgt)

        # Add Aim constraint hashes and update their indices
        for ac in getattr(p, 'aim_constraints', []):
            for idx_key in ('JointHashIndex', 'TargetHashIndex'):
                old_idx = ac.get(idx_key, -1)
                if 0 <= old_idx < len(p.hash_list):
                    ac[idx_key] = _get_or_add_hash(p.hash_list[old_idx])

        # Add RotExpression hash indices (two index arrays per entry) and update them
        for re in getattr(p, 'rot_expressions', []):
            for idx_key in ('SrcJointHashIndex', 'JntHashIndex'):
                old_idx = re.get(idx_key, -1)
                if 0 <= old_idx < len(p.hash_list):
                    re[idx_key] = _get_or_add_hash(p.hash_list[old_idx])

        # Add Material constraint JointHash indices and update them
        for mc in getattr(p, 'material_cns', []):
            old_idx = mc.get('JointHashIndex', -1)
            if 0 <= old_idx < len(p.hash_list):
                mc['JointHashIndex'] = _get_or_add_hash(p.hash_list[old_idx])

        # ── Phase 2: layout constants ───────────────────────────────────
        N = len(p.constraints)

        version = struct.unpack_from('<I', orig, 0)[0]
        layout  = LAYOUTS[version]
        cb = layout['counts_base']
        cf = layout['counts_fields']

        # ConstraintInfo start: fixed for v102, pointer-driven for v35
        CNS_INFO_START = (layout['cns_info_start']
                          if layout['cns_info_start'] is not None
                          else p.header['ConstraintSetsStart'])
        CNS_INFO_SIZE  = N * 80

        # v35 passthrough: preserve inline blob verbatim (patching only editable fields)
        if getattr(p, 'inline_blob', b''):
            return self._build_inline_blob(orig, p, CNS_INFO_START, N)

        # v35 with no Range constraints (e.g. Aim-only files): nothing editable, write orig
        if version == 35 and N == 0:
            with open(self.filepath, 'wb') as f:
                f.write(orig)
            print(f'[JCNS] Written {len(orig)} bytes → {self.filepath} (v35 pure passthrough, no Range constraints)')
            return True

        # ConstraintSource_v2 section starts right after ConstraintInfo,
        # padded to 16-byte boundary (80 is a multiple of 16, so no pad needed
        # for any N; still guard for correctness).
        raw_src_start = CNS_INFO_START + CNS_INFO_SIZE
        SRC_START = _align(raw_src_start, 16)

        # ── Phase 3: build ConstraintSource_v2 blobs ───────────────────
        src_blob      = bytearray()
        src_offsets   = []  # absolute file offset of each 72-byte Source_v2 struct

        for c in p.constraints:
            # Align each Source_v2 to 16-byte boundary
            abs_base = SRC_START + len(src_blob)
            pad = _align(abs_base, 16) - abs_base
            src_blob.extend(b'\x00' * pad)
            abs_base += pad

            abs_name = abs_base + 72          # WString immediately follows struct
            src_offsets.append(abs_base)

            block = bytearray(72)
            struct.pack_into('<Q', block,  0, c.get('ComplexMappingInfoOffset', 0))
            struct.pack_into('<Q', block,  8, abs_name)
            struct.pack_into('<I', block, 16, c.get('SourceHashIndex', 0))
            struct.pack_into('<H', block, 20, c.get('ComplexMappingInfoCount', 0))
            struct.pack_into('<H', block, 22, c.get('UnknownUInt16', 0))
            block[24] = c.get('UnkByte0', 3)
            block[25] = c.get('Interpolation', 1)
            block[26] = c.get('source_axis', 0) # bt: SourceAxis
            block[27] = c.get('UnkByte2', 0)
            struct.pack_into('<I', block, 28, c.get('UnknownUInt32_2', 0))
            struct.pack_into('<f', block, 32, c.get('from_start',  0.0))
            struct.pack_into('<f', block, 36, c.get('from_kink',   0.0))
            struct.pack_into('<f', block, 40, c.get('from_end',    0.0))
            struct.pack_into('<f', block, 44, c.get('to_start',    0.0))
            struct.pack_into('<f', block, 48, c.get('to_kink',     0.0))
            struct.pack_into('<f', block, 52, c.get('to_end',      0.0))
            struct.pack_into('<f', block, 56, c.get('rest_quat_x', 0.0))
            struct.pack_into('<f', block, 60, c.get('rest_quat_y', 0.0))
            struct.pack_into('<f', block, 64, c.get('rest_quat_z', 0.0))
            struct.pack_into('<f', block, 68, c.get('rest_quat_w', 1.0))
            src_blob.extend(block)

            # Source WString (UTF-16LE, null-terminated, 8-byte aligned)
            wstr = c.get('SourceName', '').encode('utf-16le') + b'\x00\x00'
            src_blob.extend(wstr)
            rem = (SRC_START + len(src_blob)) % 8
            if rem:
                src_blob.extend(b'\x00' * (8 - rem))

        # ── Phase 4: build target WString pool ─────────────────────────
        TGT_POOL_START = SRC_START + len(src_blob)
        tgt_pool_blob       = bytearray()
        tgt_name_to_offset  = {}   # name → absolute file offset

        for c in p.constraints:
            name = c.get('TargetBoneName', '')
            if name not in tgt_name_to_offset:
                abs_off = TGT_POOL_START + len(tgt_pool_blob)
                tgt_name_to_offset[name] = abs_off
                wstr = name.encode('utf-16le') + b'\x00\x00'
                tgt_pool_blob.extend(wstr)
                # Pad to 2-byte alignment so the next string is word-aligned
                if (TGT_POOL_START + len(tgt_pool_blob)) % 2:
                    tgt_pool_blob.extend(b'\x00')

        # Pad pool to 8-byte boundary
        rem = (TGT_POOL_START + len(tgt_pool_blob)) % 8
        if rem:
            tgt_pool_blob.extend(b'\x00' * (8 - rem))

        # ── Phase 5: build Dependency table + data ─────────────────────
        # Original file has 8 bytes of zero padding before DependencyTableEntry.
        DEP_PAD_SIZE   = 8
        DEP_TABLE_START = TGT_POOL_START + len(tgt_pool_blob) + DEP_PAD_SIZE

        # Collect unique (target_hash, source_hash) pairs.
        # Always derive target hash from the name so renamed bones get correct hashes.
        dep_pairs = []
        seen_pairs = set()
        for c in p.constraints:
            tgt_name = c.get('TargetBoneName', '')
            if _is_direct_hash(c):
                tgt_h = hashUTF16(tgt_name) & 0xFFFFFFFF if tgt_name else c.get('ObjectHash', 0)
            else:
                tgt_h, _ = _get_or_add(tgt_name)
            src_idx = c.get('SourceHashIndex', 0)
            src_h = new_hash_list[src_idx] if src_idx < len(new_hash_list) else 0
            pair = (tgt_h, src_h)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                dep_pairs.append(pair)

        M = len(dep_pairs)
        DEP_DATA_START = DEP_TABLE_START + M * 16

        dep_table_blob = bytearray()
        dep_data_blob  = bytearray()
        for i, (tgt_h, src_h) in enumerate(dep_pairs):
            data_offset = DEP_DATA_START + i * 8
            dep_table_blob.extend(struct.pack('<QQ', data_offset, 1))
            dep_data_blob.extend(struct.pack('<II', tgt_h, src_h))

        # ── Phase 6: build SectionTable ────────────────────────────────
        SEC_TABLE_START = DEP_DATA_START + len(dep_data_blob)
        # Pad to 4-byte alignment
        rem = SEC_TABLE_START % 4
        if rem:
            SEC_TABLE_START += (4 - rem)

        sc_off, sc_fmt  = cf['SectionCount']
        sec_count       = struct.unpack_from(sc_fmt, orig, cb + sc_off)[0]
        orig_sec_off    = struct.unpack_from('<Q', orig, 0xB0)[0]  # original SectionTableEntry
        section_blob    = bytearray()
        for i in range(sec_count):
            st = struct.unpack_from('<I', orig, orig_sec_off + i * 4)[0]
            section_blob.extend(struct.pack('<I', st))

        # ── Phase 7: build HashTable ────────────────────────────────────
        HASH_TABLE_START = _align(SEC_TABLE_START + len(section_blob), 16)
        hash_blob = bytearray()
        for h in new_hash_list:
            hash_blob.extend(struct.pack('<I', h))

        # ── Phase 8: build ConstraintInfo array ────────────────────────
        cns_info_blob = bytearray()
        for i, c in enumerate(p.constraints):
            block = bytearray(80)

            tgt_name    = c.get('TargetBoneName', '')
            tgt_name_off = tgt_name_to_offset.get(tgt_name, 0)
            if _is_direct_hash(c):
                tgt_h   = hashUTF16(tgt_name) & 0xFFFFFFFF if tgt_name else c.get('ObjectHash', 0)
                tgt_idx = 0xFFFFFFFF
            else:
                tgt_h, tgt_idx = _get_or_add(tgt_name)
            src_v2_off   = src_offsets[i]

            struct.pack_into('<Q', block,  0, c.get('ConeDriverInfoOffset', 0))
            struct.pack_into('<Q', block,  8, src_v2_off)
            struct.pack_into('<Q', block, 16, tgt_name_off)
            struct.pack_into('<Q', block, 24, c.get('PropertyOffset', 0))
            struct.pack_into('<I', block, 32, tgt_idx)          # TargetHashIndex
            struct.pack_into('<I', block, 36, tgt_h)            # ObjectHash
            struct.pack_into('<I', block, 40, c.get('PropertyHash', 0))
            block[44] = c.get('ConeDriverInfoCount', 0)
            block[45] = c.get('SourceCount_parent', 1)
            block[46] = c.get('Flags', 0x30)
            block[47] = c.get('TransformType', 1)
            vec4 = c.get('ParentVec4', (0.0, 0.0, 0.0, 1.0))
            struct.pack_into('<4f', block, 48, *vec4)
            f2 = c.get('ParentFloat2', (0.0, 0.0))
            struct.pack_into('<2f', block, 64, *f2)
            block[72] = c.get('ParentUInt8_72', 0)
            block[73] = c.get('TransformAxis_parent', 0)
            block[74:80] = bytes(c.get('ParentTailBytes', b'\x00' * 6))

            cns_info_blob.extend(block)

        # ── Phase 8b: build RotExpression section ───────────────────────
        rot_list = getattr(p, 'rot_expressions', [])
        N_ROT = len(rot_list)
        rot_map = getattr(p, 'rot_expression_map', b'')
        ROT_INFO_START = ROT_MAP_START = ROT_SRC_IDX_START = ROT_JNT_IDX_START = 0
        rot_blob = bytearray()
        if N_ROT > 0:
            ROT_INFO_START = _align(HASH_TABLE_START + len(hash_blob), 16)
            for re in rot_list:
                rot_blob.extend(re['info_raw'])           # 56 bytes verbatim
            ROT_MAP_START = ROT_INFO_START + len(rot_blob)
            rot_blob.extend(rot_map)
            # Align source-index table to 4 bytes
            pad = (4 - len(rot_blob) % 4) % 4
            rot_blob.extend(b'\x00' * pad)
            ROT_SRC_IDX_START = ROT_INFO_START + len(rot_blob)
            for re in rot_list:
                rot_blob.extend(struct.pack('<i', re.get('SrcJointHashIndex', -1)))
            ROT_JNT_IDX_START = ROT_INFO_START + len(rot_blob)
            for re in rot_list:
                rot_blob.extend(struct.pack('<i', re.get('JntHashIndex', -1)))

        # ── Phase 8c: build Material constraint section ──────────────────
        mat_list = getattr(p, 'material_cns', [])
        N_MAT = len(mat_list)
        MAT_START = 0
        mat_blob = bytearray()
        if N_MAT > 0:
            base_off = ROT_INFO_START + len(rot_blob) if N_ROT > 0 else HASH_TABLE_START + len(hash_blob)
            MAT_START = _align(base_off, 16)
            for mc in mat_list:
                mat_blob.extend(struct.pack('<i', mc['JointHashIndex']))  # 4 bytes (updated index)
                mat_blob.extend(mc['raw_body'])                            # 12 bytes verbatim

        # ── Phase 8d: build JointExportGraph section ─────────────────────
        jxg = getattr(p, 'joint_export_graph', None)
        JXG_START = 0
        jxg_blob = bytearray()
        if jxg is not None:
            base_off2 = MAT_START + len(mat_blob) if N_MAT > 0 else (
                        ROT_INFO_START + len(rot_blob) if N_ROT > 0 else HASH_TABLE_START + len(hash_blob))
            JXG_START = _align(base_off2, 8)
            path_wstr = jxg['path'].encode('utf-16le') + b'\x00\x00'
            path_abs = JXG_START + 8   # PathOffset uint64 is first, path data follows
            jxg_blob.extend(struct.pack('<Q', path_abs))
            jxg_blob.extend(path_wstr)
            rem = len(jxg_blob) % 8
            if rem:
                jxg_blob.extend(b'\x00' * (8 - rem))

        # ── Phase 8e: build Aim section ─────────────────────────────────
        # Aim constraints (read-only passthrough) are stored in parser.aim_constraints.
        # Each consists of a 80-byte inline block (offset+body) and a 16-byte target block.
        # We rebuild with corrected absolute offset pointers.
        aim_list = getattr(p, 'aim_constraints', [])
        N_AIM = len(aim_list)
        AIM_SECTION_START = 0
        aim_blob = bytearray()
        if N_AIM > 0:
            # Aim section comes after hash table + any preceding non-Range sections
            if jxg is not None:
                _prev_end = JXG_START + len(jxg_blob)
            elif N_MAT > 0:
                _prev_end = MAT_START + len(mat_blob)
            elif N_ROT > 0:
                _prev_end = ROT_INFO_START + len(rot_blob)
            else:
                _prev_end = HASH_TABLE_START + len(hash_blob)
            AIM_SECTION_START = _align(_prev_end, 16)
            aim_target_base = AIM_SECTION_START + N_AIM * 80
            for i, ac in enumerate(aim_list):
                tgt_abs = aim_target_base + i * 16
                aim_blob.extend(struct.pack('<Q', tgt_abs))  # offset pointer (8 bytes)
                aim_blob.extend(ac['inline_body'])            # 72 bytes
            for ac in aim_list:
                aim_blob.extend(struct.pack('<i', ac['TargetHashIndex']))  # 4 bytes
                aim_blob.extend(ac['target_body'])                         # 12 bytes

        # ── Phase 9: patch header ───────────────────────────────────────
        header = bytearray(orig[:CNS_INFO_START])

        def _patch_count(name, value):
            off, fmt = cf[name]
            struct.pack_into(fmt, header, cb + off, value)

        # DataEntry pointers (same absolute offsets for all supported versions)
        struct.pack_into('<Q', header, 0xB8, DEP_TABLE_START)
        struct.pack_into('<Q', header, 0xB0, SEC_TABLE_START)
        struct.pack_into('<Q', header, 0xC0, HASH_TABLE_START)
        struct.pack_into('<Q', header, 0x58, CNS_INFO_START)  # ConstraintInfoEntry

        # ObjectSettingEntry (0x60): when count==0 it marks Section 0 end boundary
        os_off, os_fmt    = cf['ObjectSettingCount']
        obj_setting_count = struct.unpack_from(os_fmt, orig, cb + os_off)[0]
        if obj_setting_count == 0:
            struct.pack_into('<Q', header, 0x60, SEC_TABLE_START)

        # Counts — version-aware via layout dict
        _patch_count('HashCount',       len(new_hash_list))
        _patch_count('ConstraintCount', N)
        _patch_count('DependencyCount', M)

        # AimConstraintTableEntry (0x98)
        if N_AIM > 0:
            struct.pack_into('<Q', header, 0x98, AIM_SECTION_START)
        # RotExpression sub-section offsets (0x68, 0x70, 0x78, 0x80)
        if N_ROT > 0:
            struct.pack_into('<Q', header, 0x68, ROT_INFO_START)
            struct.pack_into('<Q', header, 0x70, ROT_MAP_START)
            struct.pack_into('<Q', header, 0x78, ROT_SRC_IDX_START)
            struct.pack_into('<Q', header, 0x80, ROT_JNT_IDX_START)
        # MaterialConstraintInfoEntry (0xA0)
        if N_MAT > 0:
            struct.pack_into('<Q', header, 0xA0, MAT_START)
        # JointExportGraphInfoEntry (0xA8)
        if jxg is not None:
            struct.pack_into('<Q', header, 0xA8, JXG_START)

        # ── Phase 10: assemble ──────────────────────────────────────────
        out = bytearray()
        out.extend(header)                             # 0x00..0xEF
        out.extend(cns_info_blob)                      # ConstraintInfo[]
        _pad_to(out, SRC_START)
        out.extend(src_blob)                           # Source_v2 + source WStrings
        out.extend(tgt_pool_blob)                      # Target WString pool
        _pad_to(out, DEP_TABLE_START - DEP_PAD_SIZE)   # 8-byte padding before dep table
        out.extend(b'\x00' * DEP_PAD_SIZE)
        out.extend(dep_table_blob)                     # Dependency table
        out.extend(dep_data_blob)                      # Dependency hash pairs
        _pad_to(out, SEC_TABLE_START)
        out.extend(section_blob)                       # SectionTable
        _pad_to(out, HASH_TABLE_START)
        out.extend(hash_blob)                          # HashTable
        if N_ROT > 0:
            _pad_to(out, ROT_INFO_START)
            out.extend(rot_blob)                       # RotExpression info + map + index arrays
        if N_MAT > 0:
            _pad_to(out, MAT_START)
            out.extend(mat_blob)                       # Material constraints
        if jxg is not None:
            _pad_to(out, JXG_START)
            out.extend(jxg_blob)                       # JointExportGraph
        if N_AIM > 0:
            _pad_to(out, AIM_SECTION_START)
            out.extend(aim_blob)                       # Aim section

        with open(self.filepath, 'wb') as f:
            f.write(out)

        print(f'[JCNS] Written {len(out)} bytes → {self.filepath}')
        parts = [f'Cns={N}', f'Aim={N_AIM}', f'RotExpr={N_ROT}', f'Mat={N_MAT}',
                 f'JXG={1 if jxg else 0}', f'Dep={M}', f'Hash={len(new_hash_list)}']
        print(f'  {", ".join(parts)}')
        print(f'  SecTbl=0x{SEC_TABLE_START:X} DepTbl=0x{DEP_TABLE_START:X} HashTbl=0x{HASH_TABLE_START:X}')
        if obj_setting_count == 0:
            print(f'  ObjSettingEntry → 0x{SEC_TABLE_START:X} (count=0, synced)')
        return True


    def _build_inline_blob(self, orig, p, CNS_INFO_START, N):
        """
        v35 passthrough: preserve the inline blob (ConeDriverInfo + Source_v2 structs +
        embedded WStrings + gap data) verbatim, patching only the editable Source_v2 fields
        (mapping floats, axes, rest_quat, interpolation).  Hash list and all pointers are
        kept from the original file, so the output is byte-identical to the source except
        for fields the user changed.
        """
        cns_end   = p.inline_blob_cns_end   # = original CNS_INFO_START + original_N * 80
        dep_start = p.header['DependencyTableEntry']

        orig_cns_count = (cns_end - CNS_INFO_START) // 80

        # ── Patch Source_v2 editable fields in-place ────────────────────
        patched_blob = bytearray(p.inline_blob)
        for c in p.constraints:
            src_ptr = c.get('LimitsPointer', 0)
            if src_ptr == 0:
                continue
            off = src_ptr - cns_end
            if off < 0 or off + 72 > len(patched_blob):
                continue
            patched_blob[off + 24] = c.get('UnkByte0', 3)
            patched_blob[off + 25] = c.get('Interpolation', 1)
            patched_blob[off + 26] = c.get('source_axis', 0)
            patched_blob[off + 27] = c.get('UnkByte2', 0)
            struct.pack_into('<I', patched_blob, off + 28, c.get('UnknownUInt32_2', 0))
            struct.pack_into('<f', patched_blob, off + 32, c.get('from_start',  0.0))
            struct.pack_into('<f', patched_blob, off + 36, c.get('from_kink',   0.0))
            struct.pack_into('<f', patched_blob, off + 40, c.get('from_end',    0.0))
            struct.pack_into('<f', patched_blob, off + 44, c.get('to_start',    0.0))
            struct.pack_into('<f', patched_blob, off + 48, c.get('to_kink',     0.0))
            struct.pack_into('<f', patched_blob, off + 52, c.get('to_end',      0.0))
            struct.pack_into('<f', patched_blob, off + 56, c.get('rest_quat_x', 0.0))
            struct.pack_into('<f', patched_blob, off + 60, c.get('rest_quat_y', 0.0))
            struct.pack_into('<f', patched_blob, off + 64, c.get('rest_quat_z', 0.0))
            struct.pack_into('<f', patched_blob, off + 68, c.get('rest_quat_w', 1.0))

        # ── Rebuild ConstraintInfo with original pointers/hashes ────────
        cns_info_blob = bytearray()
        for c in p.constraints:
            block = bytearray(80)
            struct.pack_into('<Q', block,  0, c.get('ConeDriverInfoOffset', 0))
            struct.pack_into('<Q', block,  8, c.get('LimitsPointer', 0))
            struct.pack_into('<Q', block, 16, c.get('TargetBoneNameOffset', 0))
            struct.pack_into('<Q', block, 24, c.get('PropertyOffset', 0))
            struct.pack_into('<I', block, 32, c.get('TargetHashIndex', 0))
            struct.pack_into('<I', block, 36, c.get('ObjectHash', 0))
            struct.pack_into('<I', block, 40, c.get('PropertyHash', 0))
            block[44] = c.get('ConeDriverInfoCount', 0)
            block[45] = c.get('SourceCount_parent', 1)
            block[46] = c.get('Flags', 0x30)
            block[47] = c.get('TransformType', 1)
            vec4 = c.get('ParentVec4', (0.0, 0.0, 0.0, 1.0))
            struct.pack_into('<4f', block, 48, *vec4)
            f2 = c.get('ParentFloat2', (0.0, 0.0))
            struct.pack_into('<2f', block, 64, *f2)
            block[72] = c.get('ParentUInt8_72', 0)
            block[73] = c.get('TransformAxis_parent', 0)
            block[74:80] = bytes(c.get('ParentTailBytes', b'\x00' * 6))
            cns_info_blob.extend(block)

        # ── Minimal header: copy original, patch ConstraintInfoEntry ptr ─
        header = bytearray(orig[:CNS_INFO_START])
        struct.pack_into('<Q', header, 0x58, CNS_INFO_START)

        # ── Assemble ────────────────────────────────────────────────────
        out = bytearray()
        out.extend(header)
        out.extend(cns_info_blob)
        out.extend(patched_blob)
        out.extend(orig[dep_start:])   # dep table, sec table, hash table, all other sections

        with open(self.filepath, 'wb') as f:
            f.write(out)

        print(f'[JCNS] Written {len(out)} bytes → {self.filepath} (v35 inline_blob passthrough)')
        print(f'  Cns={N}, blob={len(patched_blob)} bytes, suffix={len(orig)-dep_start} bytes')
        return True


# ── Utilities ────────────────────────────────────────────────────────────

def _align(value, boundary):
    """Round value UP to the next multiple of boundary."""
    rem = value % boundary
    return value if rem == 0 else value + (boundary - rem)


def _pad_to(buf, target):
    """Extend bytearray buf with zeros until len(buf) == target."""
    if len(buf) < target:
        buf.extend(b'\x00' * (target - len(buf)))
