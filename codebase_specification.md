# RE Engine JCNS Editor - 技术规范与架构说明

## 1. 整体架构 (Collection-Based Architecture)
重构后的编辑器采用“一个文件对应一个集合”的扁平化结构，充分利用 Blender 的原生对象管理功能。

- **JCNS Collection (Green Tag)**: 顶层集合，存放一个 JCNS 文件相关的所有对象。
- **Root Empty (PLAIN_AXES)**: 集合的根节点，挂载 `JCNSRootProperties`，存储全局文件信息（如原始路径、目标骨架）。
- **Constraint Empties (ARROWS)**: 每个约束对应一个 Empty，挂载 `JCNSConstraintProperties`，存储具体的映射参数（Mapping Limits, Axes, Unknowns）。

### 数据流向
1. **Import**: `JCNSParser` (Bytes) → `Importer` → `Blender Objects/Properties`.
2. **Edit**: 用户在属性面板修改 `JCNSConstraintProperties`.
3. **Execution**: `Operators` 读取属性 → 生成 Blender Driver 表达式 → 应用到目标骨架。
4. **Export**: `Exporter` 获取 Empty 列表 → `JCNSParser` (重解析原始文件获取骨骼/哈希) → 注入修改后的属性 → `JCNSWriter` (重建二进制)。

---

## 2. 核心数据结构 (Property Groups)

### `JCNSRootProperties` (挂载在根 Empty: `obj.jcns_root_props`)
- `source_filepath`: 原始 `.jcns.102` 的绝对路径。
- `target_armature`: 目标骨架对象引用，用于批量应用驱动。

### `JCNSConstraintProperties` (挂载在约束 Empty: `obj.jcns_cns_props`)
- **Identity**: `source_bone`, `target_bone`, `transform_type` (枚举: Rotation, Location, Scale, etc).
- **Axes**: `source_axis`, `target_axis` (枚举: X, Y, Z, W).
- **Mapping Limits** (4个 Anchor 浮点数):
  - `map_from_max`: 锚点 B 的源角度（通常是 0/静止状态）。
  - `map_from_min`: 锚点 A 的源角度（通常是旋转极限）。
  - `map_to_max`: 锚点 A 对应的目标输出。
  - `map_to_min`: 锚点 B 对应的目标输出。
- **Unknowns**: `unk_float_1` ~ `unk_float_6` (保留原始数据以实现无损导出)。

---

## 3. 二进制协议 (JCNS v102)

### 约束信息块 (ConstraintInfo - 80 Bytes)
起始偏移通常在 `0xF0`。
- `+16`: `TargetBoneName` (WString 指针).
- `+32`: `TargetHashIndex` (uint32).
- `+47`: `TransformType` (uint8, 1=Rotation).
- `+73`: `TransformAxis` (uint8).

### 约束源结构 (ConstraintSource_v2 - 72 Bytes)
由父块的 `+8` 指针指向。
- `+16`: `SourceHashIndex` (uint32).
- `+24`: `source_axis` (uint8, 0=X, 1=Y, 2=Z, 3=W).
- `+26`: `target_axis` (uint8, 0=X, 1=Y, 2=Z, 3=W).
- `+32`: `MapFrom_Max` (float).
- `+36`: `MapFrom_Min` (float).
- `+40`: `MapTo_Max` (float).
- `+44`: `MapTo_Min` (float).
- `+52`: `UnkFloat2` (通常为 80.0).
- `+68`: `UnkFloat6` (通常为 1.0).

---

## 4. 关键函数与逻辑

### 映射转换公式 (Remap Formula)
源码位置: `jcns_operators.py -> _build_remap_expression`

通过两个锚点 (A, B) 计算线性插值，并强制约束在输出范围内。这保证了在静止姿态（source=0）时，如果 0 在范围外，输出会被 Clamp 为 0，从而完美保留 Rest Pose。

```python
slope = (to_min_r - to_max_r) / (from_max_r - from_min_r)
expr = f"max({out_lo}, min({out_hi}, {to_max_r} + (var - {from_min_r}) * {slope}))"
```

### 无损导出 (Lossless Writing)
源码位置: `modules/jcns_writer.py -> build_lossless`

采用“切片拼接 (Splicing)”策略：
1. 计算新约束数据块与旧块的大小差异 (`delta`)。
2. 提取并保留原始 Header 和 Tail。
3. 修正 Header 和父块中指向 Tail 区域的所有文件索引/偏移量（增加 `delta`）。
4. 将新数据注入原位置。

---

## 5. 开发建议与后续改进点
1. **W 轴支持**: 目前四元数 W 轴的驱动映射尚未实现，因为 Blender 的 Driver 变量处理 W 轴（旋转 3 号分量）需要特殊转换。
2. **多源约束 (ExtraInfo)**: 部分文件在 `ExtraCnsInfo_Offset` 有数据，表示该约束受多个源骨骼影响。目前的 Parser 能识别但 Writer 在重建此类文件时可能存在 Delta 计算偏差。
3. **坐标系适配**: 当前通过 `LOCAL_SPACE` 的 1:1 映射在大部分 RE Engine 骨架上表现良好，但若遇到非标准导出工具导出的 FBX，可能需要引入坐标修正矩阵。
