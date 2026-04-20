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
| 0      | Ranges  | Partially supported (bone << bone, one ConstraintSource, no ConeDrivers) |
| 1      | Rotation Expressions | Read-only |
| 2      | Skin Constraints | No |
| 3      | Aim Constraints | Basically no |
| 4      | Material Constraints | Read-only |
| 5      | Joint Export Graph | Read-only |

Unsuported section type will lost when imported.

## Requirements

[Blender 4.3 or higher](https://www.blender.org/download/)

## Credits

* [NSACloud](https://github.com/NSACloud) - Reference for the overall structural design of the plugin
* [XenonBaruku](https://github.com/XenonBaruku) - RE JCNS 010 Template
