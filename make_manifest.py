# -*- coding: utf-8 -*-
# Build the modpack manifest.
#
# Walks files/ and writes manifest.json:
#   "files": {
#       "<game-relative/path>": "<sha256>",   # every tree file (forward slashes)
#       "teamcenter_gen.py": "<sha256>",       # bare-name copies for the OLD,
#       "updater.py": "<sha256>", ...          # pre-tree updater to migrate from
#   }
#
# The bare-name entries let a friend still running the old 4-file updater pull
# the new tree-based updater/server, which then takes over the full-tree sync.
import os, io, json, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(HERE, "files")

# Files that also live at the repo root under their bare name so the legacy
# updater (which downloads BASE + "<bare name>") can migrate to the new system.
LEGACY = ["teamcenter_gen.py", "teamcenter_server.py", "template.html",
          "flags.json", "game_logos.json", "updater.py"]
LEGACY_SRC = {n: "BepInEx/plugins/TeamCenter/" + n for n in LEGACY}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def main():
    files = {}
    count = 0
    for root, _dirs, names in os.walk(FILES_DIR):
        for fn in names:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, FILES_DIR).replace("\\", "/")
            files[rel] = sha(p)
            count += 1

    # bare-name legacy hashes (must equal the in-tree file's hash)
    for name, src in LEGACY_SRC.items():
        if src in files:
            files[name] = files[src]

    manifest = {"files": files, "count": count}
    out = os.path.join(HERE, "manifest.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("wrote %s" % out)
    print("tree files: %d, total keys: %d" % (count, len(files)))


if __name__ == "__main__":
    main()
