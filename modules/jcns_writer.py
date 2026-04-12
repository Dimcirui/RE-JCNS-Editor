import struct
import os
import sys


class JCNSWriter:
    def __init__(self, parser_obj, filepath):
        self.parser = parser_obj
        self.filepath = filepath

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_lossless(self):
        """
        Fully rebuild the JCNS v102 file, supporting:
          - Changed source/target bone names (with hash lookup)
          - Changed mapping values, axes, rest quaternion
          - Added or deleted constraint blocks
        All sections are rebuilt from scratch; only the 80-byte Tags block
        (0x00..0x4F) and the static per-constraint fields are preserved
        from the original.
        """
        return self._build_full()

    # ------------------------------------------------------------------
    # Internal rebuild
    # ------------------------------------------------------------------

    def _build_full(self):
        p = self.parser
        orig = p.original_bytes

        # ── locate hashing module ──────────────────────────────────────
        hash_dir = os.path.join(os.path.dirname(__file__), 'hashing')
        if hash_dir not in sys.path:
            sys.path.insert(0, hash_dir)
        from mmh3.pymmh3 import hashUTF16  # noqa: F401 (runtime import)

        # ── Phase 1: build new hash list (append-only) ─────────────────
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

        # Ensure every target bone in constraints has a hash entry
        for c in p.constraints:
            tgt = c.get('TargetBoneName', '')
            if tgt:
                _get_or_add(tgt)

        # ── Phase 2: layout constants ───────────────────────────────────
        N = len(p.constraints)

        # ConstraintInfo array always at 0xF0
        CNS_INFO_START = 0xF0
        CNS_INFO_SIZE  = N * 80

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
            struct.pack_into('<Q', block,  0, c.get('ExtraCnsInfo_Offset', 0))
            struct.pack_into('<Q', block,  8, abs_name)
            struct.pack_into('<I', block, 16, c.get('SourceHashIndex', 0))
            struct.pack_into('<I', block, 20, c.get('Unknown1', 0))
            block[24] = c.get('source_axis', 0)
            block[25] = c.get('UnkByte1', 1)
            block[26] = c.get('target_axis', 0)
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

        sec_count       = struct.unpack_from('<B', orig, 0xEA)[0]
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
            tgt_h, tgt_idx = _get_or_add(tgt_name)
            tgt_name_off = tgt_name_to_offset.get(tgt_name, 0)
            src_v2_off   = src_offsets[i]

            struct.pack_into('<Q', block,  0, c.get('ExtraCnsInfo_Offset', 0))
            struct.pack_into('<Q', block,  8, src_v2_off)
            struct.pack_into('<Q', block, 16, tgt_name_off)
            struct.pack_into('<Q', block, 24, c.get('PropertyOffset', 0))
            struct.pack_into('<I', block, 32, tgt_idx)          # TargetHashIndex
            struct.pack_into('<I', block, 36, tgt_h)            # ObjectHash
            struct.pack_into('<I', block, 40, c.get('PropertyHash', 0))
            block[44] = c.get('ExtraInfoCount_parent', 0)
            block[45] = c.get('SourceCount_parent', 1)
            block[46] = c.get('UnknownFlags', 0x30)
            block[47] = c.get('TransformType', 1)
            vec4 = c.get('ParentVec4', (0.0, 0.0, 0.0, 1.0))
            struct.pack_into('<4f', block, 48, *vec4)
            f2 = c.get('ParentFloat2', (0.0, 0.0))
            struct.pack_into('<2f', block, 64, *f2)
            block[72] = c.get('ParentUInt8_72', 0)
            block[73] = c.get('TransformAxis_parent', 0)
            block[74:80] = bytes(c.get('ParentTailBytes', b'\x00' * 6))

            cns_info_blob.extend(block)

        # ── Phase 9: patch header ───────────────────────────────────────
        header = bytearray(orig[:0xF0])

        # DependencyTableEntry (0xB8)
        struct.pack_into('<Q', header, 0xB8, DEP_TABLE_START)
        # SectionTableEntry (0xB0)
        struct.pack_into('<Q', header, 0xB0, SEC_TABLE_START)
        # HashTableEntry (0xC0)
        struct.pack_into('<Q', header, 0xC0, HASH_TABLE_START)
        # HashTableItemCount (0xD0, int32)
        struct.pack_into('<i', header, 0xD0, len(new_hash_list))
        # ConstraintCount (0xD6, uint16)
        struct.pack_into('<H', header, 0xD6, N)
        # DependencyCount (0xD8, uint16)
        struct.pack_into('<H', header, 0xD8, M)

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

        with open(self.filepath, 'wb') as f:
            f.write(out)

        print(f'[JCNS] Written {len(out)} bytes → {self.filepath}')
        print(f'  Constraints: {N}, Dependencies: {M}, HashEntries: {len(new_hash_list)}')
        print(f'  SectionTable @ 0x{SEC_TABLE_START:X}, DepTable @ 0x{DEP_TABLE_START:X}, HashTable @ 0x{HASH_TABLE_START:X}')
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
