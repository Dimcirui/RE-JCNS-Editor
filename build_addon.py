"""
build_addon.py
--------------
Package the addon for Blender.

Two archive layouts are produced:

  extension (default, Blender 4.2+)
      Edit > Preferences > Get Extensions > Install from Disk
      blender_manifest.toml and the code sit at the ROOT of the archive, which is
      what `blender --command extension build` emits.  Installed this way the
      add-on can be updated and re-enabled without restarting Blender.

          RE-JCNS-Editor-extension-v0.4.1.zip
          ├── blender_manifest.toml
          ├── __init__.py
          ├── jcns_*.py
          └── modules/…

  legacy (--legacy, Blender 3.6 – 4.1)
      Edit > Preferences > Add-ons > Install...
      Everything lives inside one top-level folder and bl_info is used instead of
      the manifest.

          RE-JCNS-Editor-v0.4.1.zip
          └── RE-JCNS-Editor/
              ├── __init__.py
              └── …

Both are built from the same sources: __init__.py keeps bl_info so older Blender
still recognises it, while 4.2+ reads blender_manifest.toml and ignores bl_info.

Development-only files (tests, this script, 010 Editor templates, notes,
__pycache__) are left out of both.

Usage:
    python build_addon.py [--legacy] [--all] [output_dir]
"""

import ast
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_FOLDER_NAME = "RE-JCNS-Editor"
MANIFEST = "blender_manifest.toml"

# Files/dirs that ship to users.  Anything not listed here is excluded, so new
# development scratch files never leak into a release by accident.
INCLUDE_FILES = [
    "__init__.py",
    "jcns_drivers.py",
    "jcns_exporter.py",
    "jcns_importer.py",
    "jcns_operators.py",
    "jcns_ui.py",
    "modules_shim.py",
    "README.md",
]
INCLUDE_TREES = [
    "modules",
]
# Excluded even inside the included trees
EXCLUDE_NAMES = {"__pycache__", "test_out.jcns", "pattern_finder.py"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".bt", ".zip", ".jcns")


def get_bl_info_version():
    """Read bl_info['version'] out of __init__.py without importing bpy."""
    src = open(os.path.join(HERE, "__init__.py"), encoding="utf-8").read()
    m = re.search(r"bl_info\s*=\s*(\{.*?\n\})", src, re.S)
    if not m:
        raise SystemExit("could not find bl_info in __init__.py")
    info = ast.literal_eval(m.group(1))
    return ".".join(str(x) for x in info["version"])


def get_manifest_version():
    """Read version = "x.y.z" out of blender_manifest.toml (no tomllib on 3.10)."""
    path = os.path.join(HERE, MANIFEST)
    if not os.path.isfile(path):
        raise SystemExit("missing %s" % MANIFEST)
    for line in open(path, encoding="utf-8"):
        m = re.match(r'\s*version\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    raise SystemExit("no version field in %s" % MANIFEST)


def get_version():
    """Version string, with bl_info and the manifest checked against each other.

    They are two independent declarations of the same number; letting them drift
    means the legacy zip and the extension zip claim different versions.
    """
    a, b = get_bl_info_version(), get_manifest_version()
    if a != b:
        raise SystemExit(
            "version mismatch: bl_info says %s but %s says %s — update both" % (a, MANIFEST, b))
    return a


def wanted(path, name):
    if name in EXCLUDE_NAMES:
        return False
    if name.endswith(EXCLUDE_SUFFIXES):
        return False
    return True


def collect():
    """Yield (absolute_path, archive_relative_path) pairs."""
    for f in INCLUDE_FILES:
        src = os.path.join(HERE, f)
        if not os.path.isfile(src):
            raise SystemExit("missing required file: %s" % f)
        yield src, f

    for tree in INCLUDE_TREES:
        root_dir = os.path.join(HERE, tree)
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if wanted(dirpath, d)]
            for fn in filenames:
                if not wanted(dirpath, fn):
                    continue
                src = os.path.join(dirpath, fn)
                yield src, os.path.relpath(src, HERE).replace(os.sep, "/")


# Tags Blender accepts for type = "add-on".
# https://docs.blender.org/manual/en/dev/advanced/extensions/tags.html
ADDON_TAGS = {
    "3D View", "Add Curve", "Add Mesh", "Animation", "Bake", "Camera",
    "Compositing", "Development", "Game Engine", "Geometry Nodes",
    "Grease Pencil", "Import-Export", "Lighting", "Material", "Modeling",
    "Mesh", "Node", "Object", "Paint", "Pipeline", "Physics", "Render",
    "Rigging", "Scene", "Sculpt", "Sequencer", "System", "Text Editor",
    "Tracking", "User Interface", "UV",
}

REQUIRED_MANIFEST_KEYS = [
    "schema_version", "id", "version", "name", "tagline", "maintainer",
    "type", "blender_version_min", "license",
]


def validate_manifest():
    """Check blender_manifest.toml before shipping it.

    Blender reports manifest problems at install time with fairly terse errors,
    and the extensions server rejects the upload outright, so it is much cheaper
    to fail here.
    """
    path = os.path.join(HERE, MANIFEST)
    try:
        import tomllib
        with open(path, "rb") as f:
            m = tomllib.load(f)
    except ImportError:
        print("  (tomllib unavailable — skipping deep manifest validation)")
        return
    except Exception as exc:
        raise SystemExit("%s is not valid TOML: %s" % (MANIFEST, exc))

    errs = []
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in m:
            errs.append("missing required key: %s" % key)

    if m.get("schema_version") != "1.0.0":
        errs.append("schema_version should be \"1.0.0\", got %r" % m.get("schema_version"))

    ident = m.get("id", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", ident):
        errs.append("id %r must be a lowercase Python identifier "
                    "(it becomes bl_ext.<repo>.<id>)" % ident)

    if not re.fullmatch(r"\d+\.\d+\.\d+", str(m.get("version", ""))):
        errs.append("version %r must be MAJOR.MINOR.PATCH" % m.get("version"))

    tagline = m.get("tagline", "")
    if len(tagline) > 64:
        errs.append("tagline is %d chars, max 64" % len(tagline))
    if tagline[-1:] in ".!?,;:":
        errs.append("tagline must not end with punctuation: %r" % tagline)

    if m.get("type") not in ("add-on", "theme"):
        errs.append("type must be 'add-on' or 'theme', got %r" % m.get("type"))

    lic = m.get("license") or []
    if not isinstance(lic, list) or not lic:
        errs.append("license must be a non-empty array")
    for entry in lic:
        if not str(entry).startswith("SPDX:"):
            errs.append("license entry %r must use the SPDX: prefix" % entry)

    for tag in m.get("tags", []):
        if tag not in ADDON_TAGS:
            errs.append("unknown add-on tag %r" % tag)

    for perm, reason in (m.get("permissions") or {}).items():
        if perm not in ("files", "network", "clipboard", "camera", "microphone"):
            errs.append("unknown permission %r" % perm)
        if len(reason) > 64:
            errs.append("permission %r reason is %d chars, max 64" % (perm, len(reason)))
        if reason[-1:] in ".!?":
            errs.append("permission %r reason must not end with punctuation" % perm)

    if errs:
        raise SystemExit("%s has %d problem(s):\n  %s"
                         % (MANIFEST, len(errs), "\n  ".join(errs)))
    print("  manifest OK (id=%s, min Blender %s)"
          % (m["id"], m["blender_version_min"]))


def compile_check(files):
    """A release that cannot even be parsed is worse than no release."""
    import py_compile
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i, (src, rel) in enumerate(files):
            if src.endswith(".py"):
                try:
                    py_compile.compile(src, cfile=os.path.join(td, "%d.pyc" % i),
                                       doraise=True)
                except py_compile.PyCompileError as exc:
                    raise SystemExit("syntax error in %s:\n%s" % (rel, exc))


def write_zip(out_path, files, prefix):
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, rel in files:
            z.write(src, prefix + rel if prefix else rel)

    total = sum(os.path.getsize(s) for s, _ in files)
    print(out_path)
    print("  %d files, %.1f KB raw -> %.1f KB zipped"
          % (len(files), total / 1024.0, os.path.getsize(out_path) / 1024.0))
    for _, rel in sorted(files, key=lambda x: x[1]):
        print("    %s%s" % (prefix, rel))


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    flags = {a for a in argv if a.startswith("-")}
    out_dir = args[0] if args else HERE

    version = get_version()
    base = list(collect())
    compile_check(base)

    build_legacy = "--legacy" in flags or "--all" in flags
    build_ext = "--legacy" not in flags or "--all" in flags

    if build_ext:
        # Extension: manifest + code at the archive root, no wrapping folder.
        manifest = os.path.join(HERE, MANIFEST)
        if not os.path.isfile(manifest):
            raise SystemExit("missing %s" % MANIFEST)
        validate_manifest()
        files = [(manifest, MANIFEST)] + base
        write_zip(os.path.join(out_dir, "%s-extension-v%s.zip" % (ADDON_FOLDER_NAME, version)),
                  files, prefix="")
        if build_legacy:
            print()

    if build_legacy:
        # Legacy: everything inside one top-level folder, driven by bl_info.
        write_zip(os.path.join(out_dir, "%s-v%s.zip" % (ADDON_FOLDER_NAME, version)),
                  base, prefix="%s/" % ADDON_FOLDER_NAME)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
