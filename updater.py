# -*- coding: utf-8 -*-
# TeamCenter self-updater.
# On server start this pulls the latest mod files from the URL declared in
# update_config.json (a GitHub raw base URL). It is intentionally defensive:
# any problem at all (no config, offline, bad manifest, hash mismatch) is logged
# and ignored, so F3 always launches with whatever is already on disk.
import os, io, json, time, hashlib, urllib.request

# files that participate in auto-update
FILES = ["teamcenter_gen.py", "teamcenter_server.py", "template.html",
         "flags.json", "game_logos.json", "updater.py"]
# files whose change requires the server process to restart to take effect
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

def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "TeamCenter-Updater"})
    return urllib.request.urlopen(req, timeout=timeout).read()

def run(here):
    """Download any changed files. Returns True if a CORE file was replaced
    (the caller should then restart the process so the new code loads)."""
    cfgp = os.path.join(here, "update_config.json")
    if not os.path.isfile(cfgp):
        return False
    try:
        cfg = json.load(io.open(cfgp, encoding="utf-8"))
    except Exception as e:
        _log(here, "bad config: %s" % e); return False
    base = (cfg.get("base_url") or "").strip()
    if (not base) or ("USER/REPO" in base) or ("<" in base):   # not configured yet
        return False
    if not base.endswith("/"):
        base += "/"
    try:
        manifest = json.loads(_get(base + "manifest.json", 10).decode("utf-8"))
    except Exception as e:
        _log(here, "manifest unavailable: %s" % e); return False
    want = manifest.get("files") or {}
    core_changed = False
    for name in FILES:
        remote = want.get(name)
        if not remote:
            continue
        if _sha(os.path.join(here, name)) == remote:
            continue
        try:
            data = _get(base + name, 30)
            if hashlib.sha256(data).hexdigest() != remote:
                _log(here, "hash mismatch for %s, skipped" % name); continue
            tmp = os.path.join(here, name + ".new")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(here, name))
            _log(here, "updated %s" % name)
            if name in CORE:
                core_changed = True
        except Exception as e:
            _log(here, "download failed %s: %s" % (name, e))
    return core_changed
