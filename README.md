This addon allows for importing and exporting of RE Engine jcns (joint constraints) files from Blender.

## Supported JCNS Versions / Games

| JCNs Version | Game                                                                                 | Supported? |
| ------       | ------                                                                               | ------ |
| 11           | Resident Evil 2 Remake / Devil May Cry 5                                             | No |
| 12           | Resident Evil 3 Remake                                                               | No |
| 16           | Resident Evil 8                                                                      | No |
| 21           | Monster Hunter Rise Sunbreak                                                         | No |
| 22           | Resident Evil 4 Remake / Street Fighter 6                                            | No |
| 24           | Dragon's Dogma 2                                                                     | No |
| 29           | Monster Hunter Wilds (pre-TU4)                                                       | No |
| 35           | Resident Evil 9 / PRAGMATA SKETCHBOOK DEMO / Monster Hunter Stories 3 Trial Version  | No |
| 102          | Monster Hunter Wilds (post-TU4)                                                      | Yes |

## Supported Section Type

| ID     | Type| Supported? |
| ------ | ------ | ------ |
| 0      | Ranges  | Supported — multi-source (SourceCount > 1) read/written, no ConeDrivers |
| 1      | Rotation Expressions | Read-only |
| 2      | Skin Constraints | No |
| 3      | Aim Constraints | Basically no |
| 4      | Material Constraints | Read-only |
| 5      | Joint Export Graph | Read-only |

Unsuported section type will lost when imported.

## Mapping Diagnostics

Each source shows what its curve actually produces — the output at rest, at the kink
and at full deflection — plus a plot of the curve itself. A non-zero output at rest
means the bone is deflected before anything moves, which is almost always an authoring
slip; the panel flags it and offers a one-click fix when swapping the MapTo ends
would correct it.

Constraints sharing a bone axis are listed together under *Driven Bones*, because
Blender allows only one driver per channel and they are merged into a single one.

## L/R Mirror

Mirror signs are derived per constraint rather than from a fixed table: the input side
comes from the two bones' local frames read out of the armature, the output side also
depends on `flags_cns` bit 5, and multiplicative outputs (Scale, BlendShape) are never
negated. Checked against an unmodified game skeleton and Capcom's own `ch02_000_9000`,
this reproduces 155 of 158 shipped left/right pairs. The remainder are deliberately
asymmetric, so existing values are not overwritten unless you ask.

## Export Safety Gate

The writer rebuilds the whole file, so any structure it does not emit would be dropped
while its header pointer is copied verbatim — producing a file that looks fine but points
at unrelated data. Rather than write such a file, import warns and export refuses when it
finds:

* a constraint whose declared `SourceCount` exceeds the data actually present
* a constraint referencing `ConeDriverInfo` or `ComplexMappingInfo`
* a non-empty ConeDriver, ObjectSettings, or SkinConstraint table

`tests/roundtrip_test.py` checks this gate and verifies that every field the UI exposes
survives a parse → write → re-parse cycle. Run it against a folder of samples:

```
python tests/roundtrip_test.py path/to/samples
```

Note that output is not byte-identical to the input: the writer packs the string pools
more tightly than the shipped files, so most outputs are slightly smaller with all
downstream pointers shifted. Semantics are preserved.

## Requirements

[Blender 4.2 or higher](https://www.blender.org/download/) for the extension build,
or 3.6+ for the legacy build.

## Installation

**Blender 4.2+ (extension — recommended)**

`Edit > Preferences > Get Extensions > ⌄ > Install from Disk...` and pick
`RE-JCNS-Editor-extension-vX.Y.Z.zip`. Installing a newer build over an existing one
updates it in place, so you normally do not have to restart Blender.

**Blender 3.6 – 4.1 (legacy)**

`Edit > Preferences > Add-ons > Install...` and pick `RE-JCNS-Editor-vX.Y.Z.zip`.

Both archives come from the same sources: `__init__.py` keeps `bl_info` for older
Blender, while 4.2+ reads `blender_manifest.toml` and ignores it.

## Building

```
python build_addon.py          # extension zip
python build_addon.py --all    # extension + legacy zips
```

The build validates `blender_manifest.toml` (required keys, id form, tagline length,
SPDX licence, known tags, permission wording), byte-compiles every module, and checks
that `bl_info` and the manifest agree on the version number.

## Credits

* [NSACloud](https://github.com/NSACloud) - Reference for the overall structural design of the plugin
* [XenonBaruku](https://github.com/XenonBaruku) - RE JCNS 010 Template
