"""
jcns_validate.py
----------------
Pre-export safety checks.

The writer rebuilds a JCNS file from scratch: it emits ConstraintInfo, one
ConstraintSource_v2 per constraint, the string pools, the dependency table, the
section table, the hash table, and the RotExpression / Material / JXG / Aim
sections.  Anything it does *not* emit is silently dropped from the output while
the corresponding header pointer and count are copied verbatim from the source
file — leaving a dangling pointer into unrelated data.

That failure mode is invisible: the file writes fine, MD5 changes, and the
breakage only shows up in-game.  These checks turn it into a loud refusal.

Kept free of any `bpy` import so it can be run from a plain Python test harness.
"""


# Structures the writer cannot currently reproduce.  Each entry is
# (human-readable name, callable(parser) -> count of offending items).
def _count_truncated_sources(parser):
    """Constraints whose declared SourceCount does not match the blocks actually read.

    Multi-source constraints are fully supported, but a file can declare a
    SourceCount that runs past EOF (hand-edited files do this), in which case the
    parser reads fewer blocks than claimed and exporting would silently drop them.
    """
    return sum(1 for c in parser.constraints
               if len(c.get('sources', [])) != c.get('SourceCount_parent', 0))


def _count_cone_driver_info(parser):
    return sum(1 for c in parser.constraints
               if c.get('ConeDriverInfoCount', 0) or c.get('ConeDriverInfoOffset', 0))


def _count_complex_mapping(parser):
    return sum(1 for c in parser.constraints
               if c.get('ComplexMappingInfoCount', 0) or c.get('ComplexMappingInfoOffset', 0))


def _header_count(parser, field):
    return getattr(parser, 'header', {}).get(field, 0)


def check_exportable(parser):
    """
    Return a list of human-readable problem descriptions.  Empty list == safe to export.

    Every check here corresponds to a structure that is present in the source file
    but would be lost or corrupted by JCNSWriter.build_lossless().
    """
    problems = []

    n = _count_truncated_sources(parser)
    if n:
        names = [c.get('TargetBoneName') or '?' for c in parser.constraints
                 if len(c.get('sources', [])) != c.get('SourceCount_parent', 0)]
        problems.append(
            f"{n} 条约束声明的驱动源数量超过文件实际内容："
            f"{'、'.join(names)}。该文件本身已损坏（SourceCount 超出可用数据），"
            "导出会静默丢失缺失的驱动源。"
        )

    n = _count_cone_driver_info(parser)
    if n:
        problems.append(
            f"{n} 条约束引用了 ConeDriverInfo。写入器只会照抄原来的绝对偏移，"
            "却不会搬运它指向的数据。"
        )

    n = _count_complex_mapping(parser)
    if n:
        problems.append(
            f"{n} 个驱动源引用了 ComplexMappingInfo。写入器只会照抄原来的绝对偏移，"
            "却不会搬运它指向的数据。"
        )

    for field, label in (
        ('ConeDriverCount',           "ConeDriver 表"),
        ('ObjectSettingCount',        "ObjectSettings 表"),
        ('SkinConstraintCount',       "SkinConstraint 表"),
        ('SkinConstraintSourceCount', "SkinConstraintSource 表"),
    ):
        count = _header_count(parser, field)
        if count:
            problems.append(
                f"文件含有 {count} 条 {label} 记录（{field}）。写入器不会输出这个段落，"
                "却会原样复制它的头部指针 —— 导出的文件会指向无关数据。"
            )

    return problems


def format_problems(problems, filename=''):
    """Render check_exportable() output as a single multi-line string."""
    head = f"无法安全导出{'「' + filename + '」' if filename else ''} —— "
    head += f"发现 {len(problems)} 处不支持的结构："
    return "\n".join([head] + [f"  * {p}" for p in problems])
