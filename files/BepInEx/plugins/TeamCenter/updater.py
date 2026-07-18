# -*- coding: utf-8 -*-
# TeamCenter modpack self-updater (tree-based).
#
# Runs on server start (F3). Pulls the latest modpack from the GitHub raw base
# URL in update_config.json and syncs the WHOLE tree (dashboard + all mod DLLs +
# configs + default database), not just the dashboard files.
#
# Path mapping (manifest keys are game-relative, forward slashes):
#   "_lowdb/..."  -> the active LocalLow database. SKIPPED by the live updater
#                    so a running career is never overwritten mid-play. The
#                    installer handles that file once, with a backup.
#   everything else -> under the game root (…/Esports Manager 2026/).
#
# Loaded mod DLLs are locked by Windows while the game runs, so they cannot be
# overwritten at F3. Those are downloaded into _pending/<relpath>; the server's
# watcher applies them the moment the game process exits (see teamcenter_server).
#
# Defensive by design: any problem (no config, offline, bad manifest, hash
# mismatch, locked file) is logged and ignored so F3 always still launches.
import os, io, json, time, hashlib, urllib.request, urllib.parse

# Dashboard code files that require the python server to restart to take effect.
CORE = {"teamcenter_gen.py", "teamcenter_server.py", "updater.py"}


def _log(here, m):
    try:
        with io.open(os.path.join(here, "updater.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(m) + "\n")
    except Exception:
        pass


def _sha(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return ""


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "TeamCenter-Updater"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def game_root(here):
    # here = …/Esports Manager 2026/BepInEx/plugins/TeamCenter  ->  up 3 = game root
    return os.path.normpath(os.path.join(here, "..", "..", ".."))


def low_dir():
    return os.path.join(os.path.expanduser("~"), "AppData", "LocalLow",
                        "NeuronaGames", "EsportsManager")


def target_path(here, relpath):
    """Absolute on-disk target for a manifest relpath, or None if it must be
    skipped by the live updater (the active LocalLow database)."""
    parts = relpath.split("/")
    if parts[0] == "_lowdb":
        return None
    return os.path.join(game_root(here), *parts)


def _write_atomic(target, data):
    d = os.path.dirname(target)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = target + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)


def _stage_pending(here, relpath, data):
    """Loaded DLL is locked -> keep the new bytes under _pending/<relpath>; the
    server applies them when the game closes."""
    pend = os.path.join(here, "_pending", *relpath.split("/"))
    d = os.path.dirname(pend)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(pend, "wb") as f:
        f.write(data)


def run(here):
    """Download any changed modpack file. Returns True if a CORE dashboard file
    was replaced (caller then restarts the process so new code loads)."""
    cfgp = os.path.join(here, "update_config.json")
    if not os.path.isfile(cfgp):
        return False
    try:
        cfg = json.load(io.open(cfgp, encoding="utf-8"))
    except Exception as e:
        _log(here, "bad config: %s" % e); return False
    base = (cfg.get("base_url") or "").strip()
    if (not base) or ("USER/REPO" in base) or ("<" in base):
        return False
    if not base.endswith("/"):
        base += "/"
    try:
        manifest = json.loads(_get(base + "manifest.json", 15).decode("utf-8"))
    except Exception as e:
        _log(here, "manifest unavailable: %s" % e); return False

    want = manifest.get("files") or {}
    # tree entries are the ones with a path separator; bare-name keys are only
    # there for the pre-tree updater and are ignored here.
    tree = {k: v for k, v in want.items() if "/" in k}

    core_changed = False
    n_apply = n_pending = 0
    for relpath, remote in sorted(tree.items()):
        if relpath.startswith("_lowdb/"):
            continue                              # never clobber the live save
        tgt = target_path(here, relpath)
        if not tgt:
            continue
        if _sha(tgt) == remote:
            continue
        try:
            data = _get(base + "files/" + urllib.parse.quote(relpath, safe="/"), 120)
        except Exception as e:
            _log(here, "download failed %s: %s" % (relpath, e)); continue
        if hashlib.sha256(data).hexdigest() != remote:
            _log(here, "hash mismatch %s, skipped" % relpath); continue
        try:
            _write_atomic(tgt, data)
            n_apply += 1
            if os.path.basename(relpath) in CORE:
                core_changed = True
        except (PermissionError, OSError):
            # target locked (loaded DLL) -> stage for apply on game exit
            try:
                _stage_pending(here, relpath, data)
                n_pending += 1
            except Exception as e:
                _log(here, "stage pending failed %s: %s" % (relpath, e))
        except Exception as e:
            _log(here, "apply failed %s: %s" % (relpath, e))

    if n_apply or n_pending:
        _log(here, "sync: %d applied, %d pending (apply on game exit)"
             % (n_apply, n_pending))
    return core_changed


def apply_pending(here):
    """Apply everything staged under _pending/ to its live location. Called by
    the server once the game process has exited (DLLs no longer locked). Returns
    the number of files moved into place."""
    pend = os.path.join(here, "_pending")
    if not os.path.isdir(pend):
        return 0
    moved = 0
    for root, _dirs, files in os.walk(pend):
        for fn in files:
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, pend).replace("\\", "/")
            tgt = target_path(here, rel)
            if not tgt:
                continue
            try:
                d = os.path.dirname(tgt)
                if d and not os.path.isdir(d):
                    os.makedirs(d, exist_ok=True)
                os.replace(src, tgt)
                moved += 1
            except Exception as e:
                _log(here, "pending apply failed %s: %s" % (rel, e))
    # prune empty dirs
    for root, dirs, files in os.walk(pend, topdown=False):
        if not os.listdir(root):
            try: os.rmdir(root)
            except Exception: pass
    return moved
