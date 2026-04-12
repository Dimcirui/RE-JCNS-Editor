# JCNS Editor — 开发会话笔记

> 最后更新：2026-04-11（P2 完成）  
> 本文汇总了逆向工程分析结论、当前代码现状、已做修改、以及待办事项，用于跨会话延续开发。

---

## 1. 项目概述

**RE Engine JCNS Editor** 是一个 Blender 插件（v0.3.0），用于导入、编辑和导出 Monster Hunter Wilds 的 `.jcns.102` 关节约束文件。其核心用途是将游戏内的防穿模骨骼驱动约束可视化，并允许修改后导回游戏。

### 文件结构
```
JCNS-Editor/
├── __init__.py          — PropertyGroups 定义 + 注册入口 + 辅助函数
├── jcns_importer.py     — 导入操作符
├── jcns_exporter.py     — 导出操作符（无损拼接写回）
├── jcns_operators.py    — 驱动器管理 + 约束增删操作符
├── jcns_ui.py           — 侧边栏 UI 面板
├── jcns_drivers.py      — 空存根（逻辑已合并至 operators）
└── modules/
    ├── jcns_parser.py   — 二进制解析器
    ├── jcns_writer.py   — 无损写入器
    └── hashing/mmh3/pymmh3.py  — MurmurHash3（骨骼名哈希计算）
```

---

## 2. JCNS v102 二进制格式（已确认正确解读）

### 2.1 总体结构

（已根据新版 bt 模板 `RE_Engine_JCNS_new.bt` 校正全部字段名与结构描述）

- `0x00~0xEF`：文件 Header（版本、各区块偏移指针、计数等）
- `0x50~0xB7`：DataEntry 数组（16 个 uint64，raw 值即文件偏移，**不乘 16**）
- `0xB0`：**SectionTableEntry**（SectionCount×uint32 区段表，本文件 SectionCount=1，SectionType=0 Ranges）
- `0xB8`：**DependencyTableEntry**（DependencyList 数组：`{Offset:uint64, SourceCount:uint64}[]`，每个 Offset 指向 `{ObjectHash:uint32, SourceHash:uint32}` 数据对）
- `0xC0`：**HashTableEntry**（HashTableItemCount 个 uint32 哈希值）
- `0xD0`：HashTableItemCount（**int32**，旧误认为 uint16）
- `0xD6`：ConstraintCount（uint16）
- `0xD8`：DependencyCount（uint16）
- `0xEA`：SectionCount（uint8）
- `0xF0`：**ConstraintInfo 数组起始**，每项 80 字节

### 2.2 ConstraintInfo 块（80 字节，从 `0xF0 + n*80` 开始）

| 偏移 | 类型 | 字段 |
|------|------|------|
| +0   | uint64 | ExtraCnsInfo_Offset（指向额外约束信息，0=无） |
| +8   | uint64 | OffsetSourceList（指向 ConstraintSource_v2） |
| +16  | uint64 | ObjectNameOffset（指向 TARGET 骨骼名 UTF-16LE 字符串） |
| +24  | uint64 | PropertyOffset（通常为 0） |
| +32  | uint32 | TargetHashIndex（哈希表下标） |
| +36  | uint32 | ObjectHash（直接哈希值，与上冗余） |
| +40  | uint32 | PropertyHash |
| +44  | uint8  | ExtraInfoCount |
| +45  | uint8  | SourceCount（通常 1） |
| +46  | uint8  | UnknownFlags（始终 0x30） |
| +47  | uint8  | TransformType（0=Location, 1=Rotation, 2=Scale, 3=BlendShape…） |
| +48  | vec4   | UnknownVector（[0,0,0,1] 静止姿态四元数） |
| +64  | float[2] | UnknownFloat2（通常 [0,0]） |
| +72  | uint8  | UnknownUInt8 |
| +73  | uint8  | TransformAxis（父块级别的轴，可能与源块不同） |
| +74~79 | bytes | 尾部保留字节 |

### 2.3 ConstraintSource_v2 块（72 字节，由 +8 指针指向）

**以下为经过二进制对照 + 实机数据验证后的正确字段映射：**

| 偏移 | 类型 | 字段 | 说明 |
|------|------|------|------|
| +0   | uint64 | ExtraCnsInfo_Offset | 指向额外约束信息（0=无） |
| +8   | uint64 | SourceNameOffset | 指向 SOURCE 骨骼名 UTF-16LE |
| +16  | uint32 | SourceHashIndex | 哈希表下标 |
| +20  | uint32 | Unknown1 | 始终 0 |
| +24  | uint8  | **source_axis** | 驱动骨轴向（0=X,1=Y,2=Z,3=W） |
| +25  | uint8  | UnkByte1 | 始终 1 |
| +26  | uint8  | **target_axis** | 目标骨轴向（0=X,1=Y,2=Z,3=W） |
| +27  | uint8  | UnkByte2 | 始终 0 |
| +28  | uint32 | UnknownUInt32_2 | 始终 0 |
| +32  | float  | **from_start** | 三点曲线 A — 源角度起始边界（度） |
| +36  | float  | **from_kink** | 三点曲线 B — 折点（斜率变化处） |
| +40  | float  | **from_end** | 三点曲线 C — 源角度终止极限 |
| +44  | float  | **to_start** | 点 A 的输出值 |
| +48  | float  | **to_kink** | 折点 B 的输出值（引擎读取；观测中始终 0.0） |
| +52  | float  | **to_end** | 点 C 的输出值（目标骨极限旋转量） |
| +56  | float  | rest_quat_x | 静止姿态四元数 X（始终 0.0） |
| +60  | float  | rest_quat_y | 静止姿态四元数 Y（始终 0.0） |
| +64  | float  | rest_quat_z | 静止姿态四元数 Z（始终 0.0） |
| +68  | float  | rest_quat_w | 静止姿态四元数 W（始终 1.0） |

---

## 3. 映射逻辑：分段线性三点折线

### 3.1 核心公式

三个锚点定义两段线性映射（source→output，均以度为单位）：

```
A = (from_start, to_start)   — 第一段起始
B = (from_kink,  to_kink)    — 折点（斜率变化）
C = (from_end,   to_end)     — 第二段终止

Seg 1: source ∈ [from_start, from_kink] → 线性插值 [to_start, to_kink]
Seg 2: source ∈ [from_kink,  from_end]  → 线性插值 [to_kink,  to_end]
超出范围：clamp 到对应边界
```

`to_kink` 在所有已观测文件中始终为 0.0，使 Seg1 斜率为 0（产生"死区"效果），但引擎确实读取此值（已通过控制变量实验验证）。

### 3.2 实际数据示例（来自 ch03_012_0012.jcns.102）

| 约束 | 驱动 | From [A, B, C] | To [A, B, C] | 行为描述 |
|------|------|----------------|--------------|----------|
| L_Kata_HJ_01 Z→X | L_Thigh Z | [0°, 25°, 120°] | [0°, 0°, 80°] | 死区 0→25°，之后推挤弹开 |
| L_Back_HJ_00 X→X | L_Thigh X | [-120°, 0°, 0°] | [-120°, 0°, 0°] | 纯 1:1 无死区（B=C） |
| L_Back_HJ_00 Z→Z | L_Thigh Z | [0°, 25°, 89°] | [0°, 0°, 70°] | 死区后 0→70°（平缓） |

### 3.3 哈希机制

- RE Engine 骨骼名哈希算法：**MurmurHash3（UTF-16LE 输入，seed=0xFFFFFFFF）**
- 实现：`modules/hashing/mmh3/pymmh3.py` → `hashUTF16(name)`
- 文件中 WString 和 hash **同时存储**；引擎运行时靠 hash 查找骨骼，WString 仅作调试用
- 单向：name→hash 可计算，hash→name 不可反推（多对一）

---

## 4. 功能支持状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 导入 .jcns.102 | ✅ 完整 | 解析所有字段，创建 Root + 约束 Empty |
| 导出 .jcns.102 | ✅ 完整 | lossless 拼接策略，MD5 校验 |
| Mapping 数值修改（6 浮点） | ✅ 完整 | UI 可编辑，导出写回 |
| 轴对应修改（source/target axis） | ✅ 完整 | 导出写回文件 |
| Rest Quaternion 修改 | ✅ 完整 | 导出写回文件 |
| Transform Type 修改 | ⚠️ 部分 | UI 可编辑，driver 仅支持 Rotation |
| **Source 骨骼名修改（P0）** | ✅ 完整 | UI 可直接输入，导出时计算 hash 并在 hash_list 查找 |
| **Target 骨骼名修改（P2）** | ✅ 完整 | Writer 完全重建所有区段（ConstraintInfo、WString pool、DependencyTable、HashTable） |
| 约束块删除 | ✅ 完整 | 删后自动重新编号 |
| 约束块新增 | ⚠️ 部分 | 可创建 Empty，source_bone 现可编辑；target/hash 仍需手动 |
| Blender 驱动器（SCRIPTED） | ✅ 实现 | _build_piecewise_expr() 生成三点折线 Python 表达式 |
| W 轴驱动器 | ❌ 跳过 | 四元数 W 轴需特殊处理 |

---

## 5. 已完成的重要修改（按时间顺序）

1. **字段重命名**：`map_from_max/min`、`unk_float_1~6` → `from_start/kink/end`、`to_start/kink/end`、`rest_quat_x~w`（7 个文件）

2. **驱动器重写**：从 AVERAGE+F-Curve 改回 SCRIPTED+Python 表达式  
   `_build_piecewise_expr()` 生成形如 `(max(...) if var <= fk else max(...))` 的表达式

3. **P0：Source 骨骼名可编辑 + 下拉搜索**（2026-04-11）  
   - `jcns_ui.py`：移除 `sub.enabled = False`，source_bone 改为直接可输入  
   - `jcns_exporter.py`：`_patch_constraint_from_empty()` 新增 `hash_list` 参数，骨骼名变化时调用 `hashUTF16()` 查找对应 hash_list 索引并更新 `SourceHashIndex`；找不到时保留旧索引并打印控制台警告  
   - `__init__.py`：`JCNSRootProperties` 新增 `available_bones_json`（JSON 字符串存储 hash_list 覆盖的所有骨骼名）；`source_bone` 增加 `search=_search_source_bone` 自动补全回调（大小写不敏感，含 SUGGESTION+SORT 选项）  
   - `jcns_importer.py`：导入后从所有约束的 `SourceName` 和 `TargetBoneName` 收集去重骨骼名，写入 `available_bones_json`

4. **P2：Target 骨骼名可编辑 + Writer 全量重建**（2026-04-11）  
   - `jcns_writer.py`：完全重写为 `_build_full()`，从头按相位构建全部区段  
     Phase 1 append-only hash list → Phase 2 layout → Phase 3 ConstraintSource_v2 blobs → Phase 4 target WString pool → Phase 5 DependencyTable → Phase 6 SectionTable → Phase 7 HashTable → Phase 8 ConstraintInfo → Phase 9 header patch → Phase 10 assemble  
   - `jcns_exporter.py`：`_patch_constraint_from_empty()` 新增 target bone 变更处理（直接更新 `TargetBoneName`，writer 自动推导 hash）  
   - `__init__.py`：`target_bone` StringProperty 新增 `search=_search_target_bone` 自动补全（与 source_bone 共享同一骨骼名列表）  
   - `jcns_ui.py`：移除 target bone 字段的 `sub3.enabled = False`，改为直接可编辑  
   - **测试**：round-trip 12 条约束全部正确；P2 功能测试修改 target→`L_Kata_in_01_CH_00`，dependency count 6→5，HashIndex 正确更新为 2

---

## 6. 待办清单（优先级排序）

### 中优先级

- [ ] **P1：导入时校验 WString 与 hash 是否一致**  
  不符时控制台黄字警告（每个不符单独一行）

- [ ] **Transform Type → driver 支持 Location/Scale**  
  目前只有 Rotation 类型能生成驱动器

### 低优先级

- [ ] **W 轴支持**：四元数 W 轴驱动需要特殊处理，当前直接跳过
- [ ] **多源约束 (ExtraCnsInfo)**：Parser 能识别，Writer 的 delta 计算对此类文件可能有偏差
- [ ] **ExtraJointCount**：文件头中存在 ExtraJoint 区段，目前未解析

---

## 7. 测试资源

### 二进制样本（`E:\Data\MOD工具\JCNS\binaries\`）
- `ch03_012_0012.jcns.102` — 12 个约束，有死区，左右镜像完整，**主要测试文件**
- `ch02_017_0001.jcns.102` — 120 个约束，简单映射
- `ch02_017_0003.jcns.102` / `ch02_018_*.jcns.102` — 其他样本

### 实机数据（`E:\Data\MOD工具\JCNS\data\`）
- `jcns_bone_records.csv` — 392 帧四元数数据
- `data/0411/` — 三次控制变量实验记录（验证 from_kink/to_kink 行为）

### 参考文档（`E:\Data\MOD工具\JCNS\docs\`）
- `analysis_report.md` — 逆向工程总结报告
- `jcns_bone_mapping_rules.md` — 各辅助骨约束参数详细分析与改模指南
- `jcns_mapping_visualizer.html` — 三点折线交互可视化页面

### 010 Editor 模板

- `E:\Data\MOD工具\JCNS\templates\RE_Engine_jcns_fixed.bt`（v0.66，Sheeran 修正版）
- `E:\Data\MOD工具\JCNS\templates\RE_Engine_JCNS_new.bt`（**最新版**，原作者重构，字段名与结构描述更完整准确，已用于本次 P2 格式核对）
