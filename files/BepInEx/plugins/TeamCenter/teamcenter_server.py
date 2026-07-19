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
_httpd = None          # the running HTTP server (set in main), so the game-exit
                       # watcher can shut it down cleanly when the game closes

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
    # cache the world Top-20 + Major-MVP awards so the page can pick up NEW EMTV
    # announcements live (poll /top20) and pop the medal in without a full reload
    pl = payload.get("players") or {}
    _cache["top20"] = {nk: o["top20hist"] for nk, o in pl.items() if o.get("top20hist")}
    _cache["mmvp"] = {nk: {"n": o["majorMvp"], "e": o.get("majorMvpEvents", [])}
                      for nk, o in pl.items() if o.get("majorMvp")}
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
            if self.path.startswith("/top20"):
                get_page()   # rebuild only if the save changed (cheap otherwise) -> fresh EMTV
                self._send(200, json.dumps({"t20": _cache.get("top20", {}),
                                            "mmvp": _cache.get("mmvp", {})}).encode("utf-8"),
                           "application/json"); return
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

def _final_save():
    """Persist the full career archive from the final autosave, without the heavy
    photo/texture/logo work — so everything we accumulated is on disk before the
    server quits. The archive itself is also written on every F3/rebuild, so this
    is a belt-and-suspenders flush of the very latest state."""
    try:
        save_dir = G.latest_save()
        root = G.MP(open(os.path.join(save_dir, "SlotData.mpack"), "rb").read()).parse()
        D = G.extract(G.A(root) or [])
        G.aggregate(D, save_dir)
        G.merge_archive(D)      # writes career_archive_<save>.json (tournaments, MVP, stats, history)
        log("final career state saved after game exit")
    except Exception as e:
        log("final save err: %s" % e)

def _patch_ersfund():
    """Cap every tournament's ERS fund to a realistic (real-VRS) scale so teams settle
    around ~2000 instead of ballooning to 5k+. IDEMPOTENT: sets an absolute target
    computed from the tournament's prize + major flag, re-encoded at the SAME msgpack
    width (safe in-place patch, never grows the file). Runs after the game has closed,
    so the save is not locked; and because it's part of the shared modpack it keeps new
    careers realistic too (a friend gets the same fix automatically)."""
    import struct
    try:
        path = os.path.join(G.latest_save(), "SlotData.mpack")
        data = bytearray(open(path, "rb").read())
    except Exception as e:
        log("ERS patch: no save (%s)" % e); return
    def _tot(prize, mj):
        if mj: return 2000
        if prize <= 0: return 30
        def lp(v, a, b, oa, ob):
            if v <= a: return oa
            if v >= b: return ob
            return oa + (v - a) / (b - a) * (ob - oa)
        if prize <= 10000:   return lp(prize, 0, 10000, 30, 60)
        if prize <= 50000:   return lp(prize, 10001, 50000, 80, 160)
        if prize <= 150000:  return lp(prize, 50001, 150000, 200, 360)
        if prize <= 500000:  return lp(prize, 150001, 500000, 500, 800)
        if prize <= 1000000: return lp(prize, 500001, 1000000, 1000, 1400)
        if prize <= 1250000: return lp(prize, 1000001, 1250000, 1500, 1800)
        return 1800
    def target(prize, mj):                       # real-life calibration: current /2.75
        tiered = max(1, round(_tot(prize, mj) / 3.75))
        return max(3, int(round(tiered / 2.75)))
    class _P:                                     # position-tracking msgpack reader
        def __init__(s, d): s.d = d; s.i = 0
        def u8(s): v = s.d[s.i]; s.i += 1; return v
        def rd(s, n): v = s.d[s.i:s.i+n]; s.i += n; return v
        def pv(s):
            st = s.i; b = s.u8()
            if b < 0x80: return (b, st, s.i)
            if b >= 0xe0: return (b - 256, st, s.i)
            if b < 0x90:
                for _ in range(b & 0x0f): s.pv(); s.pv()
                return (('m',), st, s.i)
            if b < 0xa0:
                return ([s.pv() for _ in range(b & 0x0f)], st, s.i)
            if b < 0xc0: s.rd(b & 0x1f); return (('s',), st, s.i)
            if b == 0xc0: return (None, st, s.i)
            if b == 0xc2: return (False, st, s.i)
            if b == 0xc3: return (True, st, s.i)
            if b == 0xc4: s.rd(s.u8()); return (('b',), st, s.i)
            if b == 0xc5: s.rd(struct.unpack('>H', s.rd(2))[0]); return (('b',), st, s.i)
            if b == 0xc6: s.rd(struct.unpack('>I', s.rd(4))[0]); return (('b',), st, s.i)
            if b == 0xca: return (struct.unpack('>f', s.rd(4))[0], st, s.i)
            if b == 0xcb: return (struct.unpack('>d', s.rd(8))[0], st, s.i)
            if b == 0xcc: return (s.u8(), st, s.i)
            if b == 0xcd: return (struct.unpack('>H', s.rd(2))[0], st, s.i)
            if b == 0xce: return (struct.unpack('>I', s.rd(4))[0], st, s.i)
            if b == 0xcf: return (struct.unpack('>Q', s.rd(8))[0], st, s.i)
            if b == 0xd0: return (struct.unpack('>b', s.rd(1))[0], st, s.i)
            if b == 0xd1: return (struct.unpack('>h', s.rd(2))[0], st, s.i)
            if b == 0xd2: return (struct.unpack('>i', s.rd(4))[0], st, s.i)
            if b == 0xd3: return (struct.unpack('>q', s.rd(8))[0], st, s.i)
            if b == 0xd9: s.rd(s.u8()); return (('s',), st, s.i)
            if b == 0xda: s.rd(struct.unpack('>H', s.rd(2))[0]); return (('s',), st, s.i)
            if b == 0xdb: s.rd(struct.unpack('>I', s.rd(4))[0]); return (('s',), st, s.i)
            if b == 0xdc:
                n = struct.unpack('>H', s.rd(2))[0]; return ([s.pv() for _ in range(n)], st, s.i)
            if b == 0xdd:
                n = struct.unpack('>I', s.rd(4))[0]; return ([s.pv() for _ in range(n)], st, s.i)
            if b == 0xde:
                n = struct.unpack('>H', s.rd(2))[0]
                for _ in range(n): s.pv(); s.pv()
                return (('m',), st, s.i)
            if b == 0xdf:
                n = struct.unpack('>I', s.rd(4))[0]
                for _ in range(n): s.pv(); s.pv()
                return (('m',), st, s.i)
            if b in (0xd4, 0xd5, 0xd6, 0xd7, 0xd8):
                sz = {0xd4:1, 0xd5:2, 0xd6:4, 0xd7:8, 0xd8:16}[b]; s.u8(); s.rd(sz); return (('e',), st, s.i)
            if b == 0xc7: n = s.u8(); s.u8(); s.rd(n); return (('e',), st, s.i)
            if b == 0xc8: n = struct.unpack('>H', s.rd(2))[0]; s.u8(); s.rd(n); return (('e',), st, s.i)
            if b == 0xc9: n = struct.unpack('>I', s.rd(4))[0]; s.u8(); s.rd(n); return (('e',), st, s.i)
            raise ValueError("mp %02x" % b)
    def reenc(tb, nv):
        if tb == 0xca: return b'\xca' + struct.pack('>f', float(nv))
        if tb == 0xcb: return b'\xcb' + struct.pack('>d', float(nv))
        if tb == 0xcc and 0 <= nv <= 255: return b'\xcc' + bytes([nv])
        if tb == 0xcd and 0 <= nv <= 65535: return b'\xcd' + struct.pack('>H', nv)
        if tb == 0xce: return b'\xce' + struct.pack('>I', nv)
        if tb < 0x80 and 0 <= nv < 0x80: return bytes([nv])
        return None
    try:
        cat = _P(data).pv()[0][51][0]
    except Exception as e:
        log("ERS patch: parse failed (%s)" % e); return
    changed = 0
    for tt in cat:
        fl = tt[0]
        if not isinstance(fl, list) or len(fl) <= 25: continue
        f4 = fl[4][0]; f6 = fl[6][0]
        if not isinstance(f6, (int, float)): f6 = 0
        val, st, en = fl[25]
        if not isinstance(val, (int, float)): continue
        tgt = target(f6, f4 == 2)
        if int(round(val)) == tgt: continue           # already at target -> idempotent skip
        nb = reenc(data[st], tgt)
        if nb is None or len(nb) != (en - st): continue   # width mismatch -> skip safely
        data[st:en] = nb; changed += 1
    if not changed:
        log("ERS patch: funds already realistic (0 changed)"); return
    try:
        bak = path + ".bak_ersauto"
        if not os.path.exists(bak):
            import shutil; shutil.copy2(path, bak)
    except Exception:
        pass
    try:
        open(path, "wb").write(bytes(data))
        log("ERS patch: rescaled %d tournament funds to realistic VRS" % changed)
    except Exception as e:
        log("ERS patch: write failed (%s)" % e)

def _game_exit_watcher():
    """Wait for the game to close, then: (1) save the final career state, (2) apply
    any DLL/config updates that were locked while the game ran, and (3) SHUT THE
    SERVER DOWN COMPLETELY so nothing lingers on the PC. The BepInEx plugin starts
    a fresh server on the next F3 in career, which reloads everything from the
    saved archive."""
    time.sleep(20)                       # let the game actually start after F3
    while _game_running():
        time.sleep(6)
    # --- the game has exited ---
    _final_save()                        # 1) everything we did is safely on disk
    _patch_ersfund()                     # 1b) keep tournament VRS funds realistic (idempotent)
    try:                                 # 2) apply mod/config updates unlocked by exit
        import updater
        moved = updater.apply_pending(HERE)
        if moved:
            log("applied %d pending mod update(s) after game exit" % moved)
    except Exception as e:
        log("apply_pending err: %s" % e)
    # 3) fully stop the server so it stops using the PC
    log("game closed -> shutting down Team Center server")
    try:
        if _httpd is not None:
            threading.Thread(target=_httpd.shutdown, daemon=True).start()
            time.sleep(1.0)
    except Exception as e:
        log("shutdown err: %s" % e)
    os._exit(0)                          # terminate the pythonw process for good

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
    # save state + apply locked mod updates + shut down when the game closes
    threading.Thread(target=_game_exit_watcher, daemon=True).start()
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
    global _httpd
    _httpd = httpd          # let the game-exit watcher stop it cleanly
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    log("serving on %s" % url)
    httpd.serve_forever()
    log("server stopped")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log("fatal: %s" % e)
