# -*- coding: utf-8 -*-
# EM2026 Team Center — localhost server. Builds the dashboard once (heavy: photos,
# stats) and serves it at http://localhost:8777/. The page polls /live every few
# seconds; /live returns the current balance + rank read live from game memory
# (written continuously to live.json by the BepInEx plugin). So money and world
# rank update in the browser in real time, no F3 and no tab reload needed.
import os, sys, io, json, time, threading, socket, webbrowser, mmap
import http.server, socketserver
# The bundled portable (embeddable) Python does NOT auto-add the script's own folder to
# sys.path, so teamcenter_gen sitting right next to this file isn't importable by default.
# Add this folder explicitly before importing it (harmless on a normal Python install).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teamcenter_gen as G

PORT = 8777
HERE = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(HERE, "teamcenter_server.log")

_cache = {"html": None, "team": None, "save_mtime": 0.0}
_lock = threading.Lock()

def cur_save_mtime():
    try:
        return os.path.getmtime(os.path.join(G.latest_save(), "SlotData.mpack"))
    except Exception:
        return 0.0

def log(m):
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(m) + "\n")
    except Exception:
        pass

def live_values(myteam):
    try:
        lp = os.path.join(HERE, "live.json")
        if os.path.isfile(lp) and (time.time() - os.path.getmtime(lp)) < 30:
            lj = json.load(io.open(lp, encoding="utf-8"))
            return {"balance": lj.get("balance"),
                    "rank": (lj.get("ranks") or {}).get(myteam)}
    except Exception as e:
        log("live_values err: %s" % e)
    return None

def build():
    save_dir = G.latest_save()
    root = G.MP(open(os.path.join(save_dir, "SlotData.mpack"), "rb").read()).parse()
    D = G.extract(G.A(root) or [])
    G.aggregate(D, save_dir)

    res_path = os.path.join(G.DATA_DIR, "resources.assets")
    resS = res_path + ".resS"
    sig = G._sig(res_path)
    idx = G.load_cache(G.CACHE_TEX, sig)
    if idx is None:
        with open(res_path, "rb") as rf:
            mm = mmap.mmap(rf.fileno(), 0, access=mmap.ACCESS_READ)
            try: idx = G.build_tex_index(mm, mm.size())
            finally: mm.close()
        G.save_cache(G.CACHE_TEX, sig, idx)

    dbP, dbL = G.load_emdb()
    need = set(p["nick"] for p in D["roster"])
    for p in D["players"].values():
        if p.get("stats"): need.add(p["nick"])
    pcache = G.load_cache(G.CACHE_PHOTO, sig) or {}
    photos, new = dict(dbP), 0
    for nk in need:
        cp = G.custom_photo(nk)
        if cp: photos[nk] = cp; continue
        if nk in photos: continue
        b = pcache.get(nk)
        if b is None:
            pp = D["players"].get(nk, {})
            b = G.extract_photo(idx, resS, nk, pp.get("first", ""), pp.get("last", "")) or ""
            pcache[nk] = b; new += 1
        if b: photos[nk] = b
    for nk, cb in G.custom_photos_all().items():
        photos[nk] = cb
    if new: G.save_cache(G.CACHE_PHOTO, sig, pcache)
    _cache["idx"] = idx; _cache["resS"] = resS
    _cache["dbL"] = dbL

    custom_logos = G.load_cache(G.CACHE_LOGOS, sig)
    if not isinstance(custom_logos, dict):
        custom_logos = G.build_all_logos(set(D["teamRank"].keys()), D["teamFull"], idx, resS)
        G.save_cache(G.CACHE_LOGOS, sig, custom_logos)
    game_logos = G.load_game_logos()
    tlogos = dict(game_logos)
    for tnm, url in dbL.items():
        if not url: continue
        if tnm in tlogos and tlogos[tnm]: continue
        if "liquipedia" in url: continue
        tlogos[tnm] = url
    tlogos.update(custom_logos)
    team_logo = custom_logos.get(D["myTeam"]) or game_logos.get(D["myTeam"]) or G.logo_for(D["myTeam"], D["teamFull"].get(D["myTeam"], ""), idx, resS) or tlogos.get(D["myTeam"])
    payload = G.build_payload(D, photos, team_logo, tlogos)
    lv = live_values(D["myTeam"])
    if lv:
        if lv.get("balance"): payload["money"]["cash"] = lv["balance"]
        if lv.get("rank"):    payload["team"]["rank"] = lv["rank"]
        payload["live"] = True
    tpl = io.open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False)).replace("__TEAM__", D["myTeam"])
    log("built dashboard: team=%s photos=%d" % (D["myTeam"], len(photos)))
    return html, D["myTeam"]

def get_page(force=False):
    with _lock:
        m = cur_save_mtime()
        # rebuild when never built, forced, or the game has written a new save
        # (a save happens after matches/training -> stats, medals, scoreboards refresh)
        if _cache["html"] is None or force or m != _cache["save_mtime"]:
            _cache["html"], _cache["team"] = build()
            _cache["save_mtime"] = m
        return _cache["html"], _cache["team"]

_photo_mem = {}   # nick -> jpeg bytes (or b"" when known-missing)
def _photo_cached(nick, first, last):
    if not nick: return None
    if nick in _photo_mem:
        return _photo_mem[nick] or None
    idx = _cache.get("idx"); resS = _cache.get("resS")
    if not idx or not resS:
        return None
    try:
        data = G.extract_photo_raw(idx, resS, nick, first, last)
    except Exception as e:
        log("photo endpoint err %s: %s" % (nick, e)); data = None
    _photo_mem[nick] = data or b""
    return data

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        try:
            if self.path.startswith("/photo"):
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                nick = (q.get("nick") or [""])[0]
                first = (q.get("first") or [""])[0]
                last = (q.get("last") or [""])[0]
                data = _photo_cached(nick, first, last)
                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers(); self.wfile.write(data)
                else:
                    self._send(404, b"", "image/jpeg")
                return
            if self.path.startswith("/live"):
                team = _cache["team"]
                v = live_values(team) or {}
                self._send(200, json.dumps(v).encode("utf-8"), "application/json"); return
            if self.path.startswith("/version"):
                self._send(200, json.dumps({"v": cur_save_mtime()}).encode("utf-8"), "application/json"); return
            if self.path.startswith("/rebuild"):
                get_page(force=True)
                self._send(200, b'{"ok":true}', "application/json"); return
            html, _ = get_page()
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        except Exception as e:
            log("GET %s err: %s" % (self.path, e))
            try: self._send(500, ("error: " + str(e)).encode("utf-8"), "text/plain; charset=utf-8")
            except Exception: pass

def port_free(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); return True
    except OSError:
        return False
    finally:
        s.close()

def kill_running_servers():
    """Kill any other teamcenter_server process (so freshly-updated code can bind
    the port). Uses PowerShell, which is always present on Windows."""
    me = os.getpid()
    cmd = ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | '
           "Where-Object { $_.CommandLine -match 'teamcenter_server' -and $_.ProcessId -ne %d } | "
           'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"' % me)
    try:
        os.system(cmd)
    except Exception as e:
        log("kill_running_servers err: %s" % e)

GAME_PROC = "EsportsManager.exe"

def _game_running():
    """True while the game process is alive. Loaded mod DLLs stay locked until it
    exits, so pending DLL updates can only be applied afterwards."""
    try:
        import subprocess
        flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + GAME_PROC],
                             capture_output=True, text=True, creationflags=flags,
                             timeout=10).stdout or ""
        return GAME_PROC.lower() in out.lower()
    except Exception:
        return True   # unknown -> assume running, i.e. don't touch locked files

def _pending_watcher():
    """Wait for the game to close, then drop any DLL/config updates that were
    downloaded while the game held them locked into place for the next launch."""
    try:
        import updater
    except Exception:
        return
    # give the game a moment to actually start after F3
    time.sleep(20)
    while _game_running():
        time.sleep(6)
    try:
        moved = updater.apply_pending(HERE)
        if moved:
            log("applied %d pending mod update(s) after game exit" % moved)
    except Exception as e:
        log("apply_pending err: %s" % e)

def self_update():
    """Pull latest files; if core code changed, restart this process so it loads."""
    try:
        import updater
    except Exception as e:
        log("updater import err: %s" % e); return
    # apply anything left over from a previous session that is now unlocked
    try:
        updater.apply_pending(HERE)
    except Exception as e:
        log("startup apply_pending err: %s" % e)
    try:
        core_changed = updater.run(HERE)
    except Exception as e:
        log("update check err: %s" % e); return
    if core_changed and os.environ.get("TC_UPDATED") != "1":
        log("core files updated -> restarting server")
        try:
            if not port_free(PORT):
                kill_running_servers()
                time.sleep(1.5)
        except Exception:
            pass
        os.environ["TC_UPDATED"] = "1"
        try:
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except Exception as e:
            log("re-exec failed: %s" % e)

def main():
    url = "http://localhost:%d/" % PORT
    self_update()   # safe no-op unless update_config.json points at a real repo
    # apply locked mod-DLL updates as soon as the game closes (background)
    threading.Thread(target=_pending_watcher, daemon=True).start()
    if not port_free(PORT):
        log("server already running -> rebuild + open browser")
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:%d/rebuild" % PORT, timeout=60).read()
        except Exception as e:
            log("rebuild trigger err: %s" % e)
        webbrowser.open(url); return
    try:
        get_page()  # prebuild (heavy) before serving
    except Exception as e:
        log("prebuild err: %s" % e)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    log("serving on %s" % url)
    httpd.serve_forever()

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log("fatal: %s" % e)
