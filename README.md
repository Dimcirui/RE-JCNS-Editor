# Wilds JCNS Editor

A Blender add-on for importing, editing, and exporting Monster Hunter Wilds
`.jcns` (joint constraint) files.

JCNS is a joint constraint format: it always reads from joints as its control sources,
and maps them onto a variety of targets — primarily other joints, but also blendshapes,
material parameters, and RSZ object properties.

This addon focuses on Wilds' armor joint-to-joint constraints — each constraint
reads an axis of one bone and maps it, through a two-segment curve,
onto an axis of another. It turns a file into a collection of Empties
you can inspect and edit, applies the constraints as live Blender drivers
so you can pose the rig and watch them work, and writes the file back.

## Supported Versions

| JCNS Version | Game | Supported |
| --- | --- | --- |
| 102 | Monster Hunter Wilds (post-TU4) | ✅️ Yes |
| 11–35 | RE2R/DMC5, RE3R, RE8, MHRise SB, RE4R/SF6, DD2, MH Wilds pre-TU4, RE9 | ❌️ No |

The parser has a v35 layout table and much of the code is version-aware, but only
v102 is exercised and supported.

## Supported Sections

| ID | Section | Supported |
| --- | --- | --- |
| 0 | Ranges | Yes (without ConeDrivers, which never appeared in armor jcns) |
| 1 | Rotation Expressions | ❌️ |
| 2 | Skin Constraints | ❌️  |
| 3 | Aim Constraints | ❌️  |
| 4 | Material Constraints | ❌️  |
| 5 | Joint Export Graph | ❌️  |

All unsupported sections will prevent the whole file from importing — see Export Safety Gate.

## How Constraints Combine

Two rules:

* **Several sources inside one constraint** — each maps independently and the
  outputs are **summed**.
* **Several constraints on the same bone axis** — the **last one in file order
  wins outright**; the earlier ones are discarded, not blended. The add-on applies
  only the winning constraint and warns on the ones it supersedes.

Because constrant order decides that, the constraint panel shows each constraint's
position and gives you buttons to move it earlier or later.

## Mapping Diagnostics

Each source shows what its curve actually produces — the output at rest, at the
kink, and at full deflection — plus a plot of the curve. A non-zero output at rest
means the bone is deflected before anything moves, which is almost always an
authoring slip; the panel flags it and offers a one-click fix when swapping the
MapTo ends would correct it.

Constraints are grouped by driven bone under *Driven Bones*, so you can see at a
glance which ones share a channel and which of them is the one that counts.

## L/R Mirror

Mirror signs are derived per constraint rather than from a fixed table: the input
side comes from the two bones' local frames read out of the armature, the output
side also depends on `flags_cns` bit 5, and multiplicative outputs (Scale,
BlendShape) are never negated.

## Export Safety Gate

The writer rebuilds the whole file, so any structure it does not emit would be
dropped while its header pointer is copied verbatim — producing a file that looks
fine but points at unrelated data. Rather than write such a file, import warns and
export refuses when it finds:

* a constraint whose declared `SourceCount` exceeds the data actually present
* a constraint referencing `ConeDriverInfo` or `ComplexMappingInfo`
* a non-empty ConeDriver, ObjectSettings, or SkinConstraint table

Output is not byte-identical to the input: the writer packs the string pools more
tightly than the shipped files, so most outputs are slightly smaller with all
downstream pointers shifted. Semantics are preserved.

## Requirements

[Blender 4.2 or newer](https://www.blender.org/download/) for the extension build,
or 3.6+ for the legacy build.

## Installation

**Blender 4.2+ (extension — recommended)**

`Edit > Preferences > Get Extensions > ⌄ > Install from Disk...` and pick
`Wilds-JCNS-Editor-extension-vX.Y.Z.zip`. Installing a newer build over an
existing one updates it in place, so you normally do not have to restart Blender.

**Blender 3.6 – 4.1 (legacy)**

`Edit > Preferences > Add-ons > Install...` and pick `Wilds-JCNS-Editor-vX.Y.Z.zip`.

Both archives come from the same sources: `__init__.py` keeps `bl_info` for older
Blender, while 4.2+ reads `blender_manifest.toml` and ignores it.

## Building

```
python build_addon.py          # extension zip
python build_addon.py --all    # extension + legacy zips
```

The build validates `blender_manifest.toml` (required keys, id form, tagline
length, SPDX licence, known tags, permission wording), byte-compiles every module,
and checks that `bl_info` and the manifest agree on the version number.

## Credits

* [NSACloud](https://github.com/NSACloud) — reference for the overall structural
  design of the add-on
* [XenonBaruku](https://github.com/XenonBaruku) — RE JCNS 010 Editor template
