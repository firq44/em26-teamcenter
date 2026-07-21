# -*- coding: utf-8 -*-
# EM2026 Team Center generator — invoked by the BepInEx plugin on F3.
# Reads the current save + game assets, builds an HLTV-style dashboard for
# the team you are currently managing, and opens it in the browser.
import os, sys, io, glob, json, struct, base64, traceback, webbrowser, time, pickle, hashlib, re

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # TeamCenter->plugins->BepInEx->game
DATA_DIR = os.path.join(GAME_DIR, "EsportsManager_Data")
LOW_DIR  = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                        "AppData", "LocalLow", "NeuronaGames", "EsportsManager")
CA_DIR   = os.path.join(LOW_DIR, "CustomAssets")
LOG      = os.path.join(HERE, "teamcenter_gen.log")

def log(m):
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(str(m) + "\n")
    except Exception:
        pass

# ---- disk caches (keyed to resources.assets size+mtime; rebuilt on game update) ----
CACHE_TEX   = os.path.join(HERE, "texidx.cache")
CACHE_PHOTO = os.path.join(HERE, "photos.cache")
CACHE_AGG   = os.path.join(HERE, "aggregate.cache")
CACHE_AGGFILES = os.path.join(HERE, "aggfiles.cache")
CACHE_LOGOS = os.path.join(HERE, "logos.cache")

def _sig(path):
    st = os.stat(path)
    return [st.st_size, int(st.st_mtime)]

def load_cache(path, sig):
    try:
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if obj.get("sig") == sig:
            return obj.get("data")
    except Exception:
        pass
    return None

def save_cache(path, sig, data):
    try:
        with open(path, "wb") as f:
            pickle.dump({"sig": sig, "data": data}, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        log("cache save fail %s: %s" % (path, e))

# ---------------- MessagePack ----------------
class MP:
    def __init__(self, data): self.d = data; self.i = 0
    def u8(self):
        v = self.d[self.i]; self.i += 1; return v
    def read(self, n):
        v = self.d[self.i:self.i+n]; self.i += n; return v
    def parse(self):
        b = self.u8()
        if b < 0x80: return b
        if b >= 0xe0: return b - 256
        if b < 0x90: return self._map(b & 0x0f)
        if b < 0xa0: return self._arr(b & 0x0f)
        if b < 0xc0: return self.read(b & 0x1f).decode('utf-8', 'replace')
        if b == 0xc0: return None
        if b == 0xc2: return False
        if b == 0xc3: return True
        if b == 0xc4: return self.read(self.u8())
        if b == 0xc5: return self.read(struct.unpack('>H', self.read(2))[0])
        if b == 0xc6: return self.read(struct.unpack('>I', self.read(4))[0])
        if b == 0xca: return struct.unpack('>f', self.read(4))[0]
        if b == 0xcb: return struct.unpack('>d', self.read(8))[0]
        if b == 0xcc: return self.u8()
        if b == 0xcd: return struct.unpack('>H', self.read(2))[0]
        if b == 0xce: return struct.unpack('>I', self.read(4))[0]
        if b == 0xcf: return struct.unpack('>Q', self.read(8))[0]
        if b == 0xd0: return struct.unpack('>b', self.read(1))[0]
        if b == 0xd1: return struct.unpack('>h', self.read(2))[0]
        if b == 0xd2: return struct.unpack('>i', self.read(4))[0]
        if b == 0xd3: return struct.unpack('>q', self.read(8))[0]
        if b == 0xd9: return self.read(self.u8()).decode('utf-8', 'replace')
        if b == 0xda: return self.read(struct.unpack('>H', self.read(2))[0]).decode('utf-8', 'replace')
        if b == 0xdb: return self.read(struct.unpack('>I', self.read(4))[0]).decode('utf-8', 'replace')
        if b == 0xdc: return self._arr(struct.unpack('>H', self.read(2))[0])
        if b == 0xdd: return self._arr(struct.unpack('>I', self.read(4))[0])
        if b == 0xde: return self._map(struct.unpack('>H', self.read(2))[0])
        if b == 0xdf: return self._map(struct.unpack('>I', self.read(4))[0])
        if b in (0xd4, 0xd5, 0xd6, 0xd7, 0xd8):
            sz = {0xd4:1, 0xd5:2, 0xd6:4, 0xd7:8, 0xd8:16}[b]; t = self.u8(); return ('ext', t, self.read(sz))
        if b == 0xc7: n = self.u8(); t = self.u8(); return ('ext', t, self.read(n))
        if b == 0xc8: n = struct.unpack('>H', self.read(2))[0]; t = self.u8(); return ('ext', t, self.read(n))
        if b == 0xc9: n = struct.unpack('>I', self.read(4))[0]; t = self.u8(); return ('ext', t, self.read(n))
        raise ValueError("mp %02x @ %d" % (b, self.i-1))
    def _arr(self, n): return [self.parse() for _ in range(n)]
    def _map(self, n):
        m = {}
        for _ in range(n):
            k = self.parse(); k = k if isinstance(k, (str, int)) else str(k)
            m[k] = self.parse()
        return m

def parse_mpack(path):
    with open(path, "rb") as f:
        return MP(f.read()).parse()

# loose accessors
def A(o): return o if isinstance(o, list) else None
def M(o): return o if isinstance(o, dict) else None
def S(o): return o if isinstance(o, str) else None
def L(o):
    if isinstance(o, bool): return 1 if o else 0
    return int(o) if isinstance(o, (int, float)) else 0
def Dd(o):
    if isinstance(o, bool): return 1.0 if o else 0.0
    return float(o) if isinstance(o, (int, float)) else 0.0
def at(o, i):
    a = A(o)
    return a[i] if (a is not None and 0 <= i < len(a)) else None

ROLE = {1:"Главный тренер", 4:"Аналитик", 8:"Психолог", 16:"Физио", 64:"Скаут", 128:"Менеджер", 256:"PR", 512:"Стратег"}

def age_from_ext(bd, gy=2026):
    if isinstance(bd, tuple) and len(bd) == 3 and bd[0] == 'ext':
        pl = bd[2]
        if len(pl) >= 4:
            secs = (pl[0] << 24) | (pl[1] << 16) | (pl[2] << 8) | pl[3]
            by = 1970 + secs // 31557600
            return gy - by
    return None

def _year_of_ext(ext):
    if isinstance(ext, tuple) and len(ext) == 3 and ext[0] == 'ext' and len(ext[2]) >= 4:
        pl = ext[2]
        secs = (pl[0] << 24) | (pl[1] << 16) | (pl[2] << 8) | pl[3]
        return 1970 + secs // 31557600
    return None

def game_year(root):
    """Current in-game year (the game's calendar). Falls back to 2026."""
    for path in ((22, 0), (43, 1)):
        try:
            y = _year_of_ext(at(A(at(root, path[0])), path[1]))
            if y and 2000 <= y <= 2100:
                return y
        except Exception:
            pass
    return 2026

_T20_TAG = re.compile(r"<[^>]+>")
_T20_RX = re.compile(r"[Тт]оп[\-\s]?20\s+игроков\s+(\d{4}).*?[№#]\s*(\d+)\s*[—\-–]\s*(\S+)")
# The #1 (the winner) is announced NOT as "...№1 — NICK" but as a separate
# "Player of the Year" email: "Игрок <b>YEAR</b> года: <b>NICK</b>!". Without this
# the #1 player never got their year-end medal (every other rank did).
_POTY_RX = re.compile(r"[Ии]грок\s+(\d{4})\s+года\s*:\s*(.+?)\s*!*\s*$")
def parse_game_top20(root):
    """The game's OWN year-end Top-20, read straight from the EMTV news emails in
    root[1]. Ranks 2-20 come as 'Топ-20 игроков <b>YEAR</b> года: <b>№RANK</b> —
    <b>NICK</b>'; the #1 winner comes as 'Игрок <b>YEAR</b> года: <b>NICK</b>!'.
    This is the authoritative ranking the game shows (e.g. s1mple #16), NOT a
    dashboard re-computation. Returns {year(int): {rank(int): nick}}."""
    out = {}
    for e in (A(at(root, 1)) or []):
        ea = A(e)
        if not ea or len(ea) < 4:
            continue
        subj = S(at(ea, 3)) or ""
        clean = _T20_TAG.sub("", subj).strip()
        # #1 — Player of the Year
        if "оп-20 игроков" not in clean and "оп 20 игроков" not in clean:
            mp = _POTY_RX.search(clean)
            if mp:
                try:
                    yr = int(mp.group(1)); nk = mp.group(2).strip().strip("!").strip()
                except Exception:
                    nk = None
                if nk:
                    out.setdefault(yr, {})[1] = nk
            continue
        # ranks 2-20
        m = _T20_RX.search(clean)
        if not m:
            continue
        try:
            yr = int(m.group(1)); rk = int(m.group(2)); nk = m.group(3).strip()
        except Exception:
            continue
        if nk and 1 <= rk <= 40:
            out.setdefault(yr, {})[rk] = nk
    return out

def clean_nick(n):
    if not n: return ""
    if len(n) > 18: return ""
    if len(n) >= 9 and n[8] == '-': return ""
    return n

# ---------------- extract data model ----------------
def extract(root):
    D = {"myTeam": "", "players": {}, "roster": [], "teamRank": {}, "teamFull": {},
         "teamCountry": {}, "trophies": [], "team_medals": [0, 0, 0], "staff": [], "coach": None,
         "cash": 0, "txns": [], "avgAge": None, "scoreboards": [], "pointsHist": {}, "rankHist": {},
         "disp": {}}
    org = A(at(root, 22)); D["myTeam"] = S(at(org, 1)) or ""
    my = D["myTeam"]
    D["gameYear"] = game_year(root)
    D["game_top20"] = parse_game_top20(root)      # the game's own Top-20 (from EMTV news)
    # Honours are stored in root[27] (per team) / root[26] (per player). Index [1] is a
    # dict {placement -> count}, placement is 1-indexed: 1 = champion, 2 = runner-up,
    # 3 = 3rd. Key 0 = took part without a podium result (what the user called "just
    # qualified"). So real medals come from [1], NOT from [4] (which is merely the list
    # of tournaments entered).
    def medals_from(honrec):
        h = A(honrec)
        if h and len(h) > 1:
            pm = M(h[1]) or {}
            return [L(pm.get(1, 0)), L(pm.get(2, 0)), L(pm.get(3, 0))]
        return [0, 0, 0]
    D["teamMedalsAll"] = {}
    teamhonors = M(at(root, 27))
    if teamhonors:
        D["team_medals"] = medals_from(teamhonors.get(my))
        for tnm, hon in teamhonors.items():
            mm = medals_from(hon)
            if any(mm):
                D["teamMedalsAll"][str(tnm)] = mm
    teams = A(at(root, 10)) or []
    tr = []
    for t in teams:
        r = A(t)
        if not r: continue
        nm = S(at(r,0))
        if nm is None: continue
        rating = Dd(r[7]) if len(r) > 7 else 0.0
        tr.append((nm, rating))
        D["teamFull"][nm] = (S(at(r,2)) or nm) if len(r) > 2 else nm
        D["teamCountry"][nm] = (S(at(r,3)) or "") if len(r) > 3 else ""
        rk = L(at(r, 30)) if len(r) > 30 else 0   # field 30 = the game's stored world rank
        if rk > 0: D["teamRank"][nm] = rk
        # field 13 = the team's ranking-points history (a short time series of VRS-like
        # points). We turn every team's points series into a rank-over-time series below.
        ph = at(r, 13)
        inner = None
        if isinstance(ph, list) and ph:
            if isinstance(ph[0], list):      inner = ph[0]
            elif isinstance(ph[0], (int, float)): inner = ph
        if inner:
            D["pointsHist"][nm] = [Dd(x) for x in inner]
    # fallback only for teams the game left unranked
    tr.sort(key=lambda x: x[1], reverse=True)
    for k, (nm, _) in enumerate(tr): D["teamRank"].setdefault(nm, k + 1)
    # world-ranking DEVELOPMENT: at each historical points snapshot, rank every team by
    # its points that week. Gives each team a rank-over-time series (like HLTV's chart).
    ph = D["pointsHist"]
    if ph:
        span = max((len(v) for v in ph.values()), default=0)
        rh = {nm: [] for nm in ph}
        for s in range(span):
            snap = [(nm, v[s]) for nm, v in ph.items() if len(v) > s]
            snap.sort(key=lambda x: x[1], reverse=True)
            for pos, (nm, _) in enumerate(snap):
                rh[nm].append(pos + 1)
        D["rankHist"] = rh
    players = A(at(root, 9)) or []
    for pp in players:
        p = A(pp)
        if not p: continue
        nk = S(at(p,0))
        if nk is None: continue
        f1 = S(at(p,1))                 # field[1] = the game's real DISPLAY nick;
        if f1 and f1 != nk:             # field[0] (nk) is the internal key (e.g. "huNter_kovac")
            D["disp"][nk] = f1          # used only to translate the SHOWN name (photos/stats stay on nk)
        pl = {"nick": nk, "first": S(at(p,2)) or "", "last": S(at(p,3)) or "",
              "country": S(at(p,5)) or "", "team": S(at(p,6)) or "",
              "overall": L(at(p,9)), "potential": L(at(p,22)) if len(p) > 22 else 0,
              "value": L(at(p,27)) if len(p) > 27 else 0,   # market value (field 27)
              "age": age_from_ext(at(p,10) if len(p) > 10 else None), "attrs": {}, "stats": None}
        # teamless players (free agents / retired) store a placeholder overall of 100
        # in the data even when their real skill is far lower — don't trust it.
        if (not pl["team"]) and pl["overall"] == 100:
            pl["overall"] = pl["potential"] if pl["potential"] and pl["potential"] < 100 else 0
        if len(p) > 43 and L(at(p, 43)) == 1:      # career-retired flag (slot 43)
            pl["retired"] = 1
        a14 = A(at(p,14))
        if a14 and len(a14) > 0:
            ad = M(a14[0])
            if ad:
                for kk, vv0 in ad.items():
                    vv = A(vv0)
                    if vv and len(vv) > 0:
                        inner = A(vv[0])
                        if inner and len(inner) > 8:
                            pl["attrs"][str(kk)] = round(Dd(inner[8]), 1)
        D["players"][nk] = pl
    # individual career medals (1st/2nd/3rd counts) + tournament GUIDs from honours
    honors = M(at(root, 26))
    if honors:
        for nk, pl in D["players"].items():
            hrec = honors.get(nk)
            m = medals_from(hrec)
            if m[0] or m[1] or m[2]:
                pl["medals"] = m
            h = A(hrec)
            if h and len(h) > 4:
                gs = [x for x in (A(h[4]) or []) if isinstance(x, str)]
                if gs:
                    pl["tournGuids"] = gs
    D["tournaments"] = parse_tournaments_raw(root)
    D["catalog"] = catalog_base(root)
    # REAL tournament MVP: the tournament database stores the actual MVP nick in
    # slot 18 of each record — this is exactly what the game shows. Use the merged
    # catalog so recent events (whose MVP lives in the DataTournament file) count.
    D["tourn_mvp"] = {}
    for rec in full_catalog(root):
        ra = A(rec)
        if not ra or len(ra) < 19:
            continue
        nm = S(at(ra, 0)); mvp = S(at(ra, 18))
        if nm and mvp:
            D["tourn_mvp"][nm] = mvp
    D["transfers"] = parse_transfers(root)
    # real per-player career prize money (participation-based, transfer-stable)
    D["player_earnings"] = compute_player_earnings(root, D["players"], D["disp"])
    D["roster"] = sorted([p for p in D["players"].values() if p["team"] == my],
                         key=lambda p: p["overall"], reverse=True)
    ages = [float(p["age"]) for p in D["roster"] if p["age"] is not None]
    D["avgAge"] = round(sum(ages)/len(ages), 1) if ages else None
    staff = A(at(root, 21)) or []
    for ss in staff:
        s = A(ss)
        if s and len(s) > 6 and S(at(s,6)) == my:
            role = L(at(s,18)) if len(s) > 18 else 0
            it = {"nick": clean_nick(S(at(s,0))), "first": S(at(s,2)) or "", "last": S(at(s,3)) or "",
                  "country": S(at(s,5)) or "", "role": ROLE.get(role, "Стафф"), "roleId": role, "skill": L(at(s,9))}
            D["staff"].append(it)
            if role == 1: D["coach"] = it
    D["staff"].sort(key=lambda x: x["roleId"])
    fin = A(at(root, 25))
    if fin:
        # "Общий баланс" = sum of all budget buckets (Wage/Unlocated/Marketing/
        # Operational/Transfer = fin[0..4]); fin[5] is the transaction list.
        D["cash"] = sum(L(at(fin, i)) for i in range(5))
        tx = A(at(fin,5))
        if tx:
            for t in tx:
                r = A(t)
                if r and len(r) >= 6:
                    D["txns"].append([S(at(r,1)) or "", L(at(r,3)), L(at(r,4)), L(at(r,5))])
    return D

def _parse_mapstats_file(f, my):
    """Parse ONE MapStats file -> (agg{nick:[maps,k,d,a,dmg,mvp,rounds]}, scoreboards[])."""
    fa, fsb = {}, []
    try:
        r = M(parse_mpack(f))
    except Exception as e:
        log("mapstats parse fail %s: %s" % (f, e)); return fa, fsb
    if not r: return fa, fsb
    for _, recv in r.items():
        rec = A(recv)
        if not rec or len(rec) < 5: continue
        flat = M(rec[4])
        # The readable map name ("Ancient", "Mirage", "Dust2") lives at slot index [1]
        # in the rec[4] "flat" structure. The rec[2] teams structure carries a GUID
        # there instead, so the scoreboard map must be read from here.
        readable_map = None
        if flat:
            # The per-slot "MVP" field the game exposes is unreliable (near-constant,
            # so every teammate ended up with the same total). Instead we award the
            # map MVP the way HLTV/CS does at a glance: one star per map, to the single
            # highest-rated player across both teams. That produces varied, sensible
            # MVP tallies that actually track who carried.
            lines = {}
            for pk, slv in flat.items():
                sl = A(slv)
                if not sl or len(sl) < 24: continue
                nk = str(pk)
                if readable_map is None:
                    _rm = S(sl[1])
                    if _rm: readable_map = _rm
                k = L(sl[14]); dd = L(sl[15]); aa = L(sl[16]); dmg = L(sl[18])
                rr = A(sl[22]); rounds = len(rr) if rr else 0
                rating = 0.45 + 0.55*(k/max(1, dd))*(dmg/max(1, rounds or 1)/78.0)
                lines[nk] = (k, dd, aa, dmg, rounds, rating)
            mvp_nk = max(lines, key=lambda n: lines[n][5]) if lines else None
            for nk, (k, dd, aa, dmg, rounds, rating) in lines.items():
                g = fa.setdefault(nk, [0, 0, 0, 0, 0, 0, 0])
                g[0] += 1; g[1] += k; g[2] += dd; g[3] += aa; g[4] += dmg
                g[5] += 1 if nk == mvp_nk else 0
                g[6] += rounds
        teamsM = M(rec[2])
        if teamsM and my in teamsM:
            per, scores, mapn = {}, {}, None
            for tk, tvv in teamsM.items():
                tv = A(tvv)
                if not tv or len(tv) < 4: continue
                score = L(tv[1]); pd = M(tv[3])
                if not pd: continue
                lst = []
                for pk, slv in pd.items():
                    sl = A(slv)
                    if not sl or len(sl) < 24: continue
                    mapn = S(sl[1]) or mapn
                    k = L(sl[14]); dd = L(sl[15]); aa = L(sl[16]); dmg = Dd(sl[18])
                    rr = A(sl[22]); rounds = len(rr) if rr else 1
                    lst.append({"nick": str(pk), "k": k, "d": dd, "a": aa,
                                "adr": round(dmg/max(1,rounds), 1),
                                "rating": round(0.45 + 0.55*(k/max(1,dd))*(dmg/max(1,rounds)/78.0), 2),
                                "mvp": 0})
                lst.sort(key=lambda x: x["rating"], reverse=True)
                per[str(tk)] = lst; scores[str(tk)] = score
            if len(per) == 2:
                opp = [x for x in per if x != my][0]
                # star the single best-rated player of the map (across both teams)
                allpl = per[my] + per[opp]
                if allpl:
                    top = max(allpl, key=lambda x: x["rating"])
                    top["mvp"] = 1
                fsb.append({"map": readable_map or mapn or "", "my": my, "opp": opp,
                    "myScore": scores.get(my,0), "oppScore": scores.get(opp,0),
                    "myPlayers": per[my], "oppPlayers": per[opp]})
    return fa, fsb

def aggregate(D, save_dir):
    my = D["myTeam"]
    files = sorted(glob.glob(os.path.join(save_dir, "MapOverallRecord", "MapStats_*.mpack")))
    # per-file cache: only newly written match files get re-parsed, so a just-finished
    # match is picked up in ~1.5s instead of re-scanning every file (~10s)
    fc = load_cache(CACHE_AGGFILES, "v3")
    if not isinstance(fc, dict): fc = {}
    merged = {}   # nick -> [maps,k,d,a,dmg,mvp,rounds]
    sboards = []
    hist = {}     # nick -> [rating per match file, chronological] (development graph)
    changed = False
    for f in files:
        sig = _sig(f)
        ent = fc.get(f)
        if ent and ent.get("sig") == sig and ent.get("team") == my:
            fa, fsb = ent["agg"], ent["sb"]
        else:
            fa, fsb = _parse_mapstats_file(f, my)
            fc[f] = {"sig": sig, "team": my, "agg": fa, "sb": fsb}
            changed = True
        for nk, g in fa.items():
            m = merged.setdefault(nk, [0, 0, 0, 0, 0, 0, 0])
            for k in range(7): m[k] += g[k]
            rr = max(1, g[6]); ddm = max(1, g[2])   # per-match rating for the timeline
            hist.setdefault(nk, []).append(round(0.45 + 0.55*(g[1]/ddm)*(g[4]/rr/78.0), 2))
        sboards.extend(fsb)
    D["ratingHist"] = hist
    for k in list(fc.keys()):
        if k not in files:
            del fc[k]; changed = True
    if changed: save_cache(CACHE_AGGFILES, "v3", fc)
    D["scoreboards"] = sboards
    for nk, g in merged.items():
        rounds = max(1, g[6]); dd = max(1, g[2])
        st = {"maps": g[0], "k": g[1], "d": g[2], "a": g[3], "mvp": g[5],
              "kd": round(g[1]/dd, 2), "adr": round(g[4]/rounds, 1), "kpr": round(g[1]/rounds, 2),
              "rating": round(0.45 + 0.55*(g[1]/dd)*(g[4]/rounds/78.0), 2),
              "raw": [g[0], g[1], g[2], g[3], g[4], g[5], g[6]]}   # maps,k,d,a,dmg,mvp,rounds
        if nk in D["players"]:
            D["players"][nk]["stats"] = st
        else:
            D["players"][nk] = {"nick": nk, "first": "", "last": "", "country": "", "team": "",
                                "overall": 0, "potential": 0, "age": None, "attrs": {}, "stats": st}

# ---------------- textures / photos ----------------
def build_tex_index(mm, length):
    pat = b"resources.assets.resS"
    idx = {}
    i = 0
    while True:
        r = mm.find(pat, i)
        if r < 0: break
        i = r + 1
        plen_at = r - 4
        if plen_at - 12 < 0: continue
        ssize = int.from_bytes(mm[plen_at-4:plen_at], 'little', signed=True)
        soff  = int.from_bytes(mm[plen_at-12:plen_at-4], 'little', signed=True)
        for back in range(44, 220):
            st = r - back
            if st < 4: break
            nlen = int.from_bytes(mm[st:st+4], 'little', signed=True)
            if nlen < 2 or nlen > 40 or st+4+nlen > r: continue
            chunk = mm[st+4:st+4+nlen]
            if any(c < 32 or c > 126 for c in chunk): continue
            fp = (st + 4 + nlen + 3) & ~3
            if fp + 28 > length: continue
            w = int.from_bytes(mm[fp+4:fp+8], 'little', signed=True)
            h = int.from_bytes(mm[fp+8:fp+12], 'little', signed=True)
            fmt = int.from_bytes(mm[fp+20:fp+24], 'little', signed=True)
            if 0 < w <= 8192 and 0 < h <= 8192 and 0 < fmt < 64:
                name = chunk.decode('ascii', 'replace')
                if name not in idx: idx[name] = (soff, ssize, w, h, fmt)
                break
    return idx

def rgba_to_jpg_b64(buf, w, h, tw):
    from PIL import Image
    img = Image.frombytes('RGBA', (w, h), bytes(buf))
    img = img.transpose(Image.FLIP_TOP_BOTTOM)          # texture data is bottom-up
    bg = Image.new('RGBA', (w, h), (37, 27, 20, 255))    # dark composite backdrop
    comp = Image.alpha_composite(bg, img).convert('RGB')
    if w > tw:                                          # only downscale, never upscale (stays sharp)
        th = max(1, int(round(h / w * tw)))
        comp = comp.resize((tw, th), Image.LANCZOS)
    out = io.BytesIO(); comp.save(out, 'JPEG', quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode('ascii')

def rgba_to_png_b64(buf, w, h):
    from PIL import Image
    img = Image.frombytes('RGBA', (w, h), bytes(buf)).transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO(); img.save(out, 'PNG')
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode('ascii')

def read_resS(resS_path, off, size):
    with open(resS_path, 'rb') as f:
        f.seek(off); return f.read(size)

def _photo_names(nick, first, last):
    # game photo textures are named inconsistently: exact nick ("ZywOo"), or
    # nick_lastname lowercase ("niko_kovac"), or first_last. Try all variants.
    n = nick or ""; f = (first or "").lower(); l = (last or "").lower(); nl = n.lower()
    cands = [n, nl, (nl + "_" + l) if l else None, (f + "_" + l) if (f and l) else None,
             (nl + l) if l else None, (f + l) if (f and l) else None, f or None]
    out, seen = [], set()
    for x in cands:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def _tex_rgba(idx, resS_path, name):
    t = idx.get(name)
    if not t: return None
    soff, ssize, w, h, fmt = t
    if fmt != 4 or w * h * 4 != ssize: return None
    buf = read_resS(resS_path, soff, ssize)
    if len(buf) < ssize: return None
    return buf, w, h

def _tex_decode(idx, resS_path, name):
    """Return (rgba_bytes, w, h) for a texture in ANY common Unity format, not just
    uncompressed RGBA32. Compressed formats (BC7=25, DXT5/BC3=12, DXT1/BC1=10) are
    decoded via texture2ddecoder (ships with UnityPy). Output is unflipped RGBA, so
    the existing rgba_to_jpg_b64 (which flips + composites) handles it unchanged."""
    t = idx.get(name)
    if not t: return None
    soff, ssize, w, h, fmt = t
    data = read_resS(resS_path, soff, ssize)
    if len(data) < ssize: return None
    from PIL import Image
    try:
        if fmt == 4:                                   # RGBA32 (uncompressed)
            if w * h * 4 != ssize: return None
            return bytes(data), w, h
        if fmt == 3:                                   # RGB24
            if w * h * 3 != ssize: return None
            return Image.frombytes('RGB', (w, h), bytes(data)).convert('RGBA').tobytes(), w, h
        if fmt == 5:                                   # ARGB32
            if w * h * 4 != ssize: return None
            return Image.frombytes('RGBA', (w, h), bytes(data), 'raw', 'ARGB').tobytes(), w, h
        try:
            import texture2ddecoder as _t2
        except Exception:
            return None
        if fmt == 25:   raw = _t2.decode_bc7(data, w, h)       # BC7
        elif fmt == 12: raw = _t2.decode_bc3(data, w, h)       # DXT5 / BC3
        elif fmt == 10: raw = _t2.decode_bc1(data, w, h)       # DXT1 / BC1
        elif fmt == 26: raw = _t2.decode_bc6(data, w, h)       # BC6H
        else: return None
        return Image.frombytes('RGBA', (w, h), bytes(raw), 'raw', 'BGRA').tobytes(), w, h
    except Exception as e:
        log("tex decode %s fmt%d fail: %s" % (name, fmt, e))
        return None

def _ci_key(idx, name):
    # game photo textures use mixed case ("Magisk_Reif", "electroNic") while some
    # generated candidates are lowercased ("magisk_reif"). Resolve case-insensitively.
    if name in idx:
        return name
    nl = name.lower()
    cache = getattr(idx, "_lc", None)
    if cache is None:
        cache = {k.lower(): k for k in idx}
        try: idx._lc = cache
        except Exception: pass
    return cache.get(nl)

def extract_photo(idx, resS_path, nick, first="", last=""):
    for name in _photo_names(nick, first, last):
        key = _ci_key(idx, name)
        if not key: continue
        r = _tex_decode(idx, resS_path, key)
        if r:
            try:
                return rgba_to_jpg_b64(r[0], r[1], r[2], 400)   # keep near-native res (sharper cards)
            except Exception as e:
                log("jpg fail %s: %s" % (key, e))
    return None

def extract_photo_raw(idx, resS_path, nick, first="", last="", tw=256):
    # raw JPEG bytes for the on-demand /photo endpoint (any player, not just roster)
    from PIL import Image
    for name in _photo_names(nick, first, last):
        key = _ci_key(idx, name)
        if not key: continue
        r = _tex_decode(idx, resS_path, key)
        if not r: continue
        try:
            buf, w, h = r
            img = Image.frombytes('RGBA', (w, h), bytes(buf)).transpose(Image.FLIP_TOP_BOTTOM)
            bg = Image.new('RGBA', (w, h), (37, 27, 20, 255))
            comp = Image.alpha_composite(bg, img).convert('RGB')
            th = max(1, int(round(h / w * tw)))
            comp = comp.resize((tw, th), Image.LANCZOS)
            out = io.BytesIO(); comp.save(out, 'JPEG', quality=85)
            return out.getvalue()
        except Exception as e:
            log("photo raw fail %s: %s" % (name, e))
    return None

def file_b64(path):
    ext = os.path.splitext(path)[1].lower()
    mime = {'.png':'image/png', '.webp':'image/webp', '.jpg':'image/jpeg', '.jpeg':'image/jpeg'}.get(ext, 'image/png')
    with open(path, 'rb') as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode('ascii'))

def custom_photo(nick):
    pdir = os.path.join(CA_DIR, "Players")
    if not os.path.isdir(pdir): return None
    cands = glob.glob(os.path.join(pdir, nick + ".*")) + glob.glob(os.path.join(pdir, nick + "_*"))
    for cf in cands:
        if os.path.splitext(cf)[1].lower() in ('.png', '.jpg', '.jpeg', '.webp'):
            try: return file_b64(cf)
            except Exception: pass
    return None

def custom_photos_all():
    # every image in CustomAssets/Players, keyed by nick (filename before "_"),
    # so ALL custom-photo players show — not just the current roster
    pdir = os.path.join(CA_DIR, "Players")
    out = {}
    if not os.path.isdir(pdir):
        return out
    for f in os.listdir(pdir):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            continue
        nick = stem.split("_")[0].strip()
        if not nick:
            continue
        try:
            out[nick] = file_b64(os.path.join(pdir, f))
        except Exception:
            pass
    return out

def tournament_logo_b64(path, maxpx=76):
    # tournament badges render tiny (~40-52px); downscale so 100+ logos stay light
    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        if max(w, h) > maxpx:
            s = maxpx / float(max(w, h))
            img = img.resize((max(1, int(w*s)), max(1, int(h*s))), Image.LANCZOS)
        out = io.BytesIO(); img.save(out, "PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode('ascii')
    except Exception:
        try: return file_b64(path)
        except Exception: return None

def _logo_key(idx, team, full):
    # EXACT match only. Case-insensitive matching wrongly grabbed player-photo
    # textures (e.g. logo "MOUZ" -> the 400x417 photo texture "mouz"). A logo is
    # never a portrait-shaped 400x417 photo, so also reject those defensively.
    cands = [team + "_Logo", team] + ([full + "_Logo", full] if full else [])
    for nm in cands:
        if nm and nm in idx:
            w, h, fmt = idx[nm][2], idx[nm][3], idx[nm][4]
            if (w, h) == (400, 417):      # that's the player-photo dimension, not a crest
                continue
            return nm
    return None

def logo_for(team, full, idx, resS_path):
    # custom logos are named by short name OR full name (e.g. "VyaliePitony.png")
    for nm in (team, full):
        if not nm: continue
        cf = os.path.join(CA_DIR, "Teams", nm + ".png")
        if os.path.isfile(cf):
            try: return file_b64(cf)
            except Exception: pass
    key = _logo_key(idx, team, full)
    if key:
        r = _tex_decode(idx, resS_path, key)          # any format (BC7/DXT/RGBA32)
        if r:
            try: return rgba_to_png_b64(r[0], r[1], r[2])
            except Exception as e: log("logo fail %s: %s" % (team, e))
    return None

def _scale_png_b64(buf_rgba, w, h, maxpx):
    from PIL import Image
    img = Image.frombytes('RGBA', (w, h), bytes(buf_rgba)).transpose(Image.FLIP_TOP_BOTTOM)
    if max(w, h) > maxpx:
        s = maxpx / float(max(w, h))
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    out = io.BytesIO(); img.save(out, 'PNG')
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode('ascii')

def logo_small(team, full, idx, resS_path, maxpx=130):
    # downscaled crest for the ranking list / team cards (keeps 500 logos light)
    for nm in (team, full):
        if not nm: continue
        cf = os.path.join(CA_DIR, "Teams", nm + ".png")
        if os.path.isfile(cf):
            try: return tournament_logo_b64(cf, maxpx)
            except Exception: pass
    key = _logo_key(idx, team, full)
    if key:
        r = _tex_decode(idx, resS_path, key)          # any format (BC7/DXT/RGBA32)
        if r:
            try: return _scale_png_b64(r[0], r[1], r[2], maxpx)
            except Exception as e: log("logo_small fail %s: %s" % (team, e))
    return None

EMDB_KEY = bytes.fromhex("E47A2C9F01D85B33A6F27EC4980D4B613EB5792ADF148C506FC3279105E6BA48")

def _find_emdb():
    for c in (os.path.join(LOW_DIR, "EM26_database.emdb"),
              os.path.join(GAME_DIR, "EsportsManager_Data", "StreamingAssets", "default.emdb")):
        if os.path.isfile(c):
            return c
    return None

def load_emdb():
    """Decrypt the game's roster database and return (player_photo_urls, team_logo_urls).
    The .emdb is AES-256-GCM(EMDB magic + version + 12B IV + ct+tag) over a ZIP of CSVs."""
    p = _find_emdb()
    if not p:
        return {}, {}
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import zipfile, csv as _csv
        d = open(p, "rb").read()
        if d[:4] != b"EMDB":
            return {}, {}
        pt = AESGCM(EMDB_KEY).decrypt(d[5:17], d[17:], None)
        z = zipfile.ZipFile(io.BytesIO(pt))
        names = z.namelist()
        def col_map(fn):
            m = {}
            if fn not in names:
                return m
            rows = list(_csv.reader(io.StringIO(z.read(fn).decode("utf-8-sig")), delimiter=";"))
            if not rows:
                return m
            H = [h.replace("﻿", "").strip() for h in rows[0]]   # tolerate stray/double BOM
            try:
                ni = H.index("Nick"); pi = H.index("PhotoUrl")
            except ValueError:
                return m
            for r in rows[1:]:
                if len(r) > pi and r[pi].strip():
                    m[r[ni]] = r[pi].strip()
            return m
        return col_map("Players.csv"), col_map("Teams.csv")
    except Exception as e:
        log("emdb load fail: %s" % e)
        return {}, {}

def load_flags():
    # country flags embedded as data-URIs (fetched once to flags.json) so they never
    # depend on a live CDN — bulk flagcdn requests were rate-limited to blank.
    p = os.path.join(HERE, "flags.json")
    try:
        if os.path.isfile(p):
            return json.load(io.open(p, encoding="utf-8"))
    except Exception as e:
        log("flags load fail: %s" % e)
    return {}

# ---- authoritative tournament results live in a SEPARATE file, not SlotData ----
# The game keeps the real per-tournament final standings / MVP / bracket in
# <save>/DataTournament/Tournaments_<year>.mpack (a dict keyed by tournament name).
# SlotData's root[51] is only a partial mirror whose [16]/[23] are EMPTY for many
# finished events (especially recent + smaller ones), which is why those events
# showed as "upcoming" with no winner/trophies/MVP. We merge the DataTournament
# file in so every finished event surfaces.
_DT_CACHE = {}
def load_datatournament():
    try:
        sd = latest_save()
    except Exception:
        return {}
    import glob as _glob
    dtdir = os.path.join(sd, "DataTournament")
    files = sorted(_glob.glob(os.path.join(dtdir, "Tournaments_*.mpack")))
    # Signature = each file's path + mtime + size. The game rewrites these files
    # every time it saves (a tournament finishing writes a new result), so keying
    # the cache on the signature makes the dashboard pick up new tournaments LIVE
    # on the very next rebuild — no server restart needed.
    try:
        sig = tuple((f, os.path.getmtime(f), os.path.getsize(f)) for f in files)
    except Exception:
        sig = tuple(files)
    cached = _DT_CACHE.get(sd)
    if cached and cached[0] == sig:
        return cached[1]
    out = {}
    for f in files:
        try:
            d = MP(open(f, "rb").read()).parse()
            if isinstance(d, dict):
                for nm, rec in d.items():
                    if isinstance(nm, str):
                        out[nm] = rec            # later years overwrite same-name (fine)
        except Exception as e:
            log("datatournament %s: %s" % (f, e))
    _DT_CACHE[sd] = (sig, out)
    return out

def full_catalog(root):
    """SlotData root[51] merged with the authoritative DataTournament records.
    For any event that has a real result in the DataTournament file (its [16]
    final standings or [23] team results), that fuller record wins; otherwise the
    root[51] record is kept. Returns a list of raw tournament records."""
    cat, order = {}, []
    for e in (A(at(root, 51)) or []):
        a = A(e)
        if a and isinstance(a[0], str):
            if a[0] not in cat:
                order.append(a[0])
            cat[a[0]] = e
    for nm, rec in load_datatournament().items():
        a = A(rec)
        if not a:
            continue
        has_res = bool(A(at(a, 16))) or bool(M(at(a, 23)))
        if has_res or nm not in cat:
            if nm not in cat:
                order.append(nm)
            cat[nm] = rec
    return [cat[nm] for nm in order]

def parse_tournament_pstats(a):
    """Per-tournament player stats from field[24]: each player value is
    [5, name, STATS, True, team] where STATS[1]=kills, STATS[2]=deaths and a
    [rating, n] pair (0.2..3.5) holds the player's tournament rating. Returns
    {nick,team,k,d,kd,rating} sorted by rating desc. Empty until an event is played."""
    pls = M(at(a, 24)) or {}
    out = []
    for nick, pv in pls.items():
        if not nick or _is_guid_nick(nick):
            continue
        pa = A(pv)
        if not pa or len(pa) < 5:
            continue
        sa = A(at(pa, 2))
        if not sa:
            continue
        k = L(at(sa, 1)); d = L(at(sa, 2)); team = S(at(pa, 4)) or ""
        rating = 0
        for it in sa:
            ia = A(it)
            if isinstance(ia, list) and len(ia) == 2 and isinstance(ia[0], float) \
               and isinstance(ia[1], int) and 0.2 <= ia[0] <= 3.5:
                rating = round(ia[0], 2)
        kd = round(k / d, 2) if d else float(k)
        out.append({"nick": nick, "team": team, "k": k, "d": d, "kd": kd, "rating": rating})
    out.sort(key=lambda x: (x["rating"], x["kd"]), reverse=True)
    return out

def parse_tournaments_raw(root):
    """Authoritative tournament results straight from the game's tournament
    catalog at root[51]. Each record (len>=26):
        [0]  name              [4]  tier (0=T1,1=lower,2=Major)
        [6]  total prizefund   [9]  country          [10] city
        [16] (LEGACY) playoff final standings — the game USED to store the finished
             bracket here, but a game update moved it out and [16] is now always
             empty, which made every completed event show up as "upcoming" with no
             winner/MVP/trophies.
        [23] TEAM RESULTS -> {team: [placement, name, stats, flag, None]}. This is
             the live, authoritative final table now: placement 1 = champion, 2 =
             runner-up ... 8 = last paid spot, 0 = didn't reach the paid playoff.
        [18] tournament MVP nick   [22] tier code
    We surface an event as finished once [23] contains a champion (a team at
    placement 1). Prize per team is the prizefund split by the game's fixed
    placement fractions (see _PRIZE_FRAC). Field [15] holds the real STAGES: a
    Swiss/group block and a playoff block, parsed into t['swiss'] and t['bracket']."""
    out = []
    # index root[51] by name: the per-player tournament stats (field [24]) live in
    # SlotData root[51], but full_catalog may prefer the DataTournament copy whose [24]
    # is empty — so we fall back to the root[51] entry for pstats.
    _r51 = {}
    for _e in (A(at(root, 51)) or []):
        _a = A(_e)
        if _a and isinstance(_a[0], str):
            _r51.setdefault(_a[0], _a)
    for e in full_catalog(root):
        a = A(e)
        if not a or len(a) < 24 or not isinstance(a[0], str):
            continue
        name = _real_tname(a)          # resolve GUID-keyed new-season events to their real name
        pf = L(at(a, 6))
        # prefer the exact final standings [16] (team, place, prize, points) from
        # the DataTournament file; fall back to [23] team results (prize derived).
        fin = A(at(a, 16))
        teamres = M(at(a, 23)) or {}
        standings = {}
        table = []
        has_champ = False
        if fin:
            for row in fin:
                r = A(row)
                if not r or len(r) < 2 or not S(at(r, 0)):
                    continue
                tm = S(at(r, 0)); pl = L(at(r, 1))
                standings[tm] = pl
                if pl == 1:
                    has_champ = True
                table.append({"team": tm, "place": pl,
                              "prize": L(at(r, 2)) if len(r) > 2 else 0,
                              "pts": L(at(r, 3)) if len(r) > 3 else 0})
        elif teamres:
            for tm, tv in teamres.items():
                tva = A(tv)
                if not tva or not tm:
                    continue
                pl = L(at(tva, 0))
                if pl <= 0:
                    continue                # 0 = eliminated before the paid playoff
                standings[tm] = pl
                if pl == 1:
                    has_champ = True
                table.append({"team": tm, "place": pl,
                              "prize": int(round(pf * _PRIZE_FRAC.get(pl, 0))),
                              "pts": 0})
        if not table or not has_champ:
            continue                        # not finished yet -> no real winner
        table.sort(key=lambda x: x["place"])
        champ = table[0]["team"]
        swiss, bracket = _parse_stages(at(a, 15), champ)
        ps = parse_tournament_pstats(a)
        if not ps:                                 # DataTournament copy has no [24] -> use root[51]
            alt = _r51.get(a[0]) or _r51.get(name)
            if alt is not None and alt is not a:
                ps = parse_tournament_pstats(alt)
        out.append({"name": name, "guid": S(at(a, 22)) or "",
                    "standings": standings, "table": table,
                    "swiss": swiss, "bracket": bracket, "pstats": ps,
                    "mvp": S(at(a, 18)) or "", "tier": L(at(a, 4)),
                    "prize": pf,
                    "city": S(at(a, 10)) or "", "country": S(at(a, 9)) or ""})
    return out

def _stage_rounds(inner):
    """The per-round pairings dict lives as the 3rd item of a stage's inner list:
    inner = [teamCount, [rows], {roundKey: [[.,.,.,.,[[a,b,guid],...], ext]]}].
    Return an ordered list of rounds, each a list of (teamA, teamB) tuples."""
    rd_map = None
    for x in inner:
        if isinstance(x, dict):
            rd_map = x; break
    if not rd_map:
        return []
    rounds = []
    for k in sorted(rd_map.keys(), key=lambda z: int(z) if str(z).lstrip("-").isdigit() else 0):
        rv = A(rd_map[k])
        cell = A(rv[0]) if rv else None      # [n,n,n,n,[pairings], ext]
        if not cell:
            continue
        pairings = None
        for it in cell:                       # find the list-of-pairs element
            ia = A(it)
            if isinstance(ia, (list, tuple)) and ia and isinstance(A(ia[0]), (list, tuple)):
                first = A(ia[0])
                if len(first) >= 2 and isinstance(first[0], str) and isinstance(first[1], str):
                    pairings = ia; break
        if pairings is None:
            continue
        matches = []
        for pr in pairings:
            pa = A(pr)
            if pa and len(pa) >= 2 and isinstance(pa[0], str) and isinstance(pa[1], str):
                matches.append((pa[0], pa[1]))
        if matches:
            rounds.append(matches)
    return rounds

def _parse_stages(f15, champion):
    """Parse the real Swiss standings and the real playoff bracket from field [15].
    Returns (swiss, bracket):
      swiss   = [{team, w, l, adv, elim, pts}]  (group/Swiss stage, real records)
      bracket = [ [ {a,b,wa,wb}, ... ], ... ]   (playoff rounds, first -> final,
                 winners derived from who advances / the champion)."""
    stages = A(f15)
    if not stages:
        return [], []
    swiss, bracket = [], []
    for st in stages:
        sa = A(st)
        if not sa or len(sa) < 2:
            continue
        stage_id = L(at(sa, 0))
        inner = A(at(sa, 1))
        if not inner or len(inner) < 2:
            continue
        rows = A(at(inner, 1)) or []
        # a stage whose teams carry a played/W/L record and no single-elim shape
        # is the Swiss/group stage; the other is the playoff.
        parsed_rows = []
        for r in rows:
            ra = A(r)
            if not ra or not isinstance(ra[0], str):
                continue
            parsed_rows.append({"team": ra[0], "played": L(at(ra, 1)),
                                "w": L(at(ra, 2)), "l": L(at(ra, 3)),
                                "pts": L(at(ra, 5)),
                                "elim": bool(L(at(ra, 6))), "adv": bool(L(at(ra, 7)))})
        rounds = _stage_rounds(inner)
        if stage_id == 0:
            # Swiss / group stage: keep the standings (sorted advanced-first)
            parsed_rows.sort(key=lambda x: (0 if x["adv"] else (2 if x["elim"] else 1),
                                            -x["w"], x["l"], -x["pts"]))
            swiss = parsed_rows
        else:
            # playoff: build the bracket from the real round pairings
            bracket = _bracket_from_rounds(rounds, champion)
    return swiss, bracket

def _bracket_from_rounds(rounds, champion):
    """rounds = ordered [ [(a,b), ...], ... ] first->final. A team that appears in
    the next round won its match; the final's winner is the champion."""
    out = []
    for i, matches in enumerate(rounds):
        nxt = set()
        if i + 1 < len(rounds):
            for a, b in rounds[i + 1]:
                nxt.add(a); nxt.add(b)
        ms = []
        for a, b in matches:
            if i + 1 < len(rounds):
                wa, wb = (a in nxt), (b in nxt)
            else:
                wa, wb = (a == champion), (b == champion)
            ms.append({"a": a, "b": b, "wa": wa, "wb": wb})
        out.append(ms)
    return out

def _is_guid_nick(s):
    """A regen/youth player's 'nick' is stored as a raw GUID (36 chars,
    hyphens at 8/13/18/23). These have no real identity and the frontend
    hides them, so we drop them from the transfer feed here too."""
    if not s or len(s) != 36:
        return False
    return s[8] == "-" and s[13] == "-" and s[18] == "-" and s[23] == "-"

def _real_tname(a):
    """Display/base name of a tournament record. A repeated (new-season) event is
    stored by the game under a GUID at field[0] but keeps its REAL name at [1] (and
    mirrored at [21]); fall back to those so a second-cycle event shows its real name
    instead of a raw GUID — it then flows through the normal per-season "(сезон N)"
    separation. The GUID stays the catalog KEY upstream, so seasons remain distinct."""
    n0 = a[0] if (a and isinstance(a[0], str)) else ""
    if n0 and not _is_guid_nick(n0):
        return n0
    for idx in (1, 21):
        v = S(at(a, idx))
        if v and not _is_guid_nick(v):
            return v
    return n0

def _ext_ts(v):
    """Decode a msgpack ext (the game stores dates as ext-typed big-endian unix seconds)."""
    if isinstance(v, (tuple, list)) and len(v) >= 3 and v[0] == "ext" and isinstance(v[2], (bytes, bytearray)):
        try:
            return int.from_bytes(bytes(v[2]), "big")
        except Exception:
            return None
    return None

def parse_transfers(root):
    """root[5] = world transfers: [.,.,age,NICK,None,TO_team,FROM_team,agentfee,FEE,...].
    field[7] is a smaller side amount (agent/weekly); field[8] is the actual transfer
    fee the game shows (verified vs in-game: huNter 253.5K, Staehr 520K, s1mple 234K).
    Most moves are free (fee=0, e.g. out-of-contract signings) — those are real and
    kept; only GUID regens are skipped."""
    out = []
    for e in (A(at(root, 5)) or []):
        a = A(e)
        if not a or len(a) < 9:
            continue
        nick = S(at(a, 3))
        if not nick or _is_guid_nick(nick):
            continue
        out.append({"nick": nick, "to": S(at(a, 5)) or "", "from": S(at(a, 6)) or "",
                    "fee": L(at(a, 8)), "age": L(at(a, 2)),
                    "date": _ext_ts(a[1] if len(a) > 1 else None) or _ext_ts(a[0] if a else None)})
    return out

def build_club_history(transfers, players, tourn_winner=None):
    """HLTV-style club history per player, from the transfer log:
      - each stint = {team, from, to (unix; to=None means Present), days}
      - trophies the player won WHILE at that team (a title the player won was won with
        the team that won it, so we group the player's won titles by that winner team)
      - totals: teams count, days in current team, days across all teams
    Rebuilt every build, so a new transfer updates it immediately. Keyed by internal key."""
    tourn_winner = tourn_winner or {}
    now_ts = 0
    moves = {}                                   # display nick -> [transfer dict, ...]
    for t in (transfers or []):
        nk = t.get("nick")
        if not nk or _is_guid_nick(nk):
            continue
        if t.get("date"):
            now_ts = max(now_ts, t["date"])
        moves.setdefault(nk, []).append(t)
    DAY = 86400.0
    hist = {}
    for pk, p in players.items():
        cur = p.get("team")
        disp = p.get("nick", pk)
        mv = moves.get(disp) or moves.get(pk)
        if not cur:
            continue
        mvs = sorted([m for m in (mv or []) if m.get("to")], key=lambda m: m.get("date") or 0)
        # build stints (team, start, end) oldest -> newest from the move chain
        stints = []
        if mvs:
            ff = mvs[0].get("from")
            if ff:
                stints.append([ff, None, mvs[0].get("date")])       # team before the first logged move
            for i, m in enumerate(mvs):
                start = m.get("date")
                end = mvs[i + 1].get("date") if i + 1 < len(mvs) else None   # None = still there
                stints.append([m.get("to"), start, end])
        else:
            stints.append([cur, None, None])                        # never moved in this career
        merged = []
        for tm, s, e in stints:
            if not tm:
                continue
            if merged and merged[-1][0] == tm:
                merged[-1][2] = e
            else:
                merged.append([tm, s, e])
        if len(merged) < 2:                       # nothing interesting to show
            continue
        won_by_team = {}
        for n, mj in (p.get("won") or []):
            w = tourn_winner.get(n)
            if w:
                won_by_team.setdefault(w, []).append({"n": n, "m": 1 if mj else 0})
        periods = []
        days_total = 0
        for tm, s, e in merged:
            end = e if e else (now_ts or None)
            days = int((end - s) / DAY) if (s and end and end > s) else None
            if days:
                days_total += days
            periods.append({"team": tm, "from": s, "to": e,
                            "days": days, "trophies": won_by_team.get(tm, [])})
        periods.reverse()                         # current team first (HLTV order)
        hist[pk] = {"periods": periods, "teams": len(merged),
                    "days_current": periods[0].get("days"), "days_total": days_total}
    return hist

# The game pays out prize money only to the top 8 of each event, always on the same
# split of the total prize fund (verified against the game's own archived payouts):
_PRIZE_FRAC = {1: 0.40, 2: 0.20, 3: 0.10, 4: 0.10, 5: 0.05, 6: 0.05, 7: 0.05, 8: 0.05}

def compute_player_earnings(root, players, disp):
    """REAL personal career prize money — stays with the player across transfers.

    The game does NOT store a per-player money figure anywhere, so the old dashboard
    faked it by splitting the *current* team's season prize across the *current*
    roster (hence every team-mate showed the same number and it jumped to the new
    club's figure the moment a player transferred).

    Instead we reconstruct each player's own earnings from history: every tournament
    in the catalog (root[51]) records, in field [24], which TEAM each player was on
    at that event and the team's final PLACEMENT. The prize a team won = prizefund
    (field [6]) x the fixed placement split above. We credit each player an equal
    share of their team's prize for every event they actually played, and sum it
    over their career. Because it's tied to where the player really was, it no
    longer changes when they move clubs."""
    # map a [24] roster key (usually the display nick) back to our internal player key
    disp2int = {v: k for k, v in (disp or {}).items()}
    def resolve(pk):
        if pk in players:
            return pk
        return disp2int.get(pk, pk)
    earn = {}
    for e in full_catalog(root):
        a = A(e)
        if not a or len(a) < 25 or not isinstance(a[0], str):
            continue
        pf = L(at(a, 6))
        if not pf:
            continue
        pls = M(at(a, 24)) or {}
        if not pls:
            continue
        # how many players each team fielded at this event (to split its prize evenly)
        counts = {}
        parsed = []
        for pk, pv in pls.items():
            pa = A(pv)
            if not pa or len(pa) < 5:
                continue
            place = L(at(pa, 0))
            team = S(at(pa, 4))
            frac = _PRIZE_FRAC.get(place, 0)
            if not frac or not team:
                continue
            counts[team] = counts.get(team, 0) + 1
            parsed.append((pk, team, frac))
        for pk, team, frac in parsed:
            cnt = counts.get(team, 5) or 5
            share = pf * frac / cnt
            ik = resolve(pk)
            earn[ik] = earn.get(ik, 0.0) + share
    return {k: int(round(v)) for k, v in earn.items()}

def load_tournament_majors():
    p = _find_emdb()
    if not p:
        return set()
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import zipfile, csv as _csv
        d = open(p, "rb").read()
        if d[:4] != b"EMDB":
            return set()
        pt = AESGCM(EMDB_KEY).decrypt(d[5:17], d[17:], None)
        z = zipfile.ZipFile(io.BytesIO(pt))
        if "Tournaments.csv" not in z.namelist():
            return set()
        rows = list(_csv.reader(io.StringIO(z.read("Tournaments.csv").decode("utf-8-sig")), delimiter=";"))
        H = rows[0]; ni = H.index("Name") if "Name" in H else 1
        ti = H.index("Tier") if "Tier" in H else -1
        majors = set()
        for r in rows[1:]:
            if len(r) <= ni:
                continue
            nm = r[ni]
            tier = r[ti] if (0 <= ti < len(r)) else ""
            # Follow the GAME's own DB classification: the Tier column is authoritative.
            # Events flagged "MAJOR" in the DB (IEM Cologne, Perfect World Shanghai Major,
            # NT World Championship) are the Majors. We deliberately do NOT fall back to
            # matching "major" in the name — a "... Major" event that has been demoted in
            # the DB (e.g. StarLadder Budapest Major, moved to a normal top tier so two
            # Majors stop colliding on the calendar) must stop counting as a Major
            # everywhere: calendar badge, team Major-champion badge and player Major-MVP.
            if str(tier).strip().upper() == "MAJOR":
                majors.add(nm)
        return majors
    except Exception as e:
        log("majors load fail: %s" % e)
        return set()

def tournament_icon(name):
    tdir = os.path.join(CA_DIR, "Tournaments")
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        f = os.path.join(tdir, name + ext)
        if os.path.isfile(f):
            try:
                return tournament_logo_b64(f, 110)
            except Exception:
                pass
    return None

def _seed_order(n):
    """Standard single-elimination seeding order for a bracket of size n (power of 2).
    Guarantees seed 1 and seed 2 can only meet in the final."""
    pods = [1]
    while len(pods) < n:
        length = len(pods) * 2
        nxt = []
        for p in pods:
            nxt.append(p)
            nxt.append(length + 1 - p)
        pods = nxt
    return pods

def build_bracket(table):
    """Reconstruct a single-elimination playoff bracket from final standings.
    The game saves only the final placement (place/prize/points) of completed
    tournaments — NOT the individual series scores. But placement maps cleanly
    onto the round a team was knocked out in (1=champion, 2=lost final,
    3-4=lost semis, 5-8=lost quarters, ...), so we can faithfully rebuild the
    bracket structure (who advanced to each round) using placement as the seed.
    Returns a list of rounds (first -> final), each a list of
    {a,b,wa,wb} matches, or None when it isn't a bracket-shaped result."""
    n = len(table or [])
    if n < 2:
        return None
    bn = 1
    while bn < n:
        bn *= 2
    if bn > 16:          # bigger fields are leagues/swiss, not a clean SE bracket
        return None
    place_of, team_by_seed = {}, {}
    for i, row in enumerate(table):
        team_by_seed[i + 1] = row["team"]        # final place doubles as seed
        place_of[row["team"]] = row.get("place", i + 1)
    slots = [team_by_seed.get(s) for s in _seed_order(bn)]
    def winner(a, b):
        if a is None: return b
        if b is None: return a
        return a if place_of.get(a, 999) <= place_of.get(b, 999) else b
    rounds, cur = [], slots
    while len(cur) > 1:
        matches, nxt = [], []
        for i in range(0, len(cur), 2):
            a, b = cur[i], cur[i + 1]
            w = winner(a, b)
            matches.append({"a": a, "b": b,
                            "wa": (w is not None and w == a),
                            "wb": (w is not None and w == b)})
            nxt.append(w)
        rounds.append(matches)
        cur = nxt
    return rounds

def compute_tournament_awards(D):
    """HLTV-style TOURNAMENT MVP / EVP counts per player (not per-map).
    The game doesn't store these awards, so we reconstruct them from final
    standings + rosters: the MVP of an event is the star (highest-overall)
    player of the team that WON it; EVPs are the rest of the winner's core
    plus the runner-up's star. Returns {nick: {"mvp": n, "evp": n}}."""
    awards = {}
    def bump(nk, key, event):
        if not nk: return
        a = awards.setdefault(nk, {"mvp": 0, "evp": 0, "mvpEvents": [], "evpEvents": []})
        a[key] += 1
        if event:
            a[key + "Events"].append(event)
    def top_players(team, k):
        pls = [p for p in D["players"].values() if p.get("team") == team and p.get("nick")]
        pls.sort(key=lambda p: p.get("overall", 0), reverse=True)
        return pls[:k]
    for t in D.get("tournaments", []):
        st = t.get("standings") or {}
        if not st:
            continue
        winner = next((tm for tm, pl in st.items() if pl == 1), None)
        if not winner:
            continue
        wp = top_players(winner, 5)
        if not wp:
            continue
        ev = t.get("name") or ""
        bump(wp[0]["nick"], "mvp", ev)         # tournament MVP = winner's star
        for e in wp[1:3]:                       # a couple of the winner's other stars
            bump(e["nick"], "evp", ev)
        runner = next((tm for tm, pl in st.items() if pl == 2), None)
        if runner:
            rp = top_players(runner, 1)
            if rp:
                bump(rp[0]["nick"], "evp", ev)  # runner-up's star also an EVP
    return awards

def compute_trophies(D):
    """Fill team + player 'won' lists (tournaments finished 1st) and collect icons.
    Returns (team_won{team:[(name,major)]}, icons{name:dataURI})."""
    majors = load_tournament_majors()
    tourns = D.get("tournaments", [])
    # display name -> base (un-suffixed) name, for Major checks + icon file lookup
    base_of = {t["name"]: t.get("base", t["name"]) for t in tourns}
    def _isMaj(nm):
        return base_of.get(nm, nm) in majors
    guid2t = {t["guid"]: t for t in tourns if t.get("guid")}
    team_won = {}
    for t in tourns:
        mj = _isMaj(t["name"])
        for tm, pl in t["standings"].items():
            if pl == 1:
                team_won.setdefault(tm, []).append((t["name"], mj))
    # players: a tournament GUID in their honours where their team finished 1st
    for nk, p in D["players"].items():
        gs = p.get("tournGuids") or []
        if not gs:
            continue
        won = []
        for g in gs:
            t = guid2t.get(g)
            if not t:
                continue
            if t["standings"].get(p.get("team")) == 1:
                won.append((t["name"], _isMaj(t["name"])))
        if won:
            p["won"] = won
    # gather icons for every won tournament (teams + players), deduped
    names = set()
    for lst in team_won.values():
        for nm, _ in lst:
            names.add(nm)
    for p in D["players"].values():
        for nm, _ in (p.get("won") or []):
            names.add(nm)
    # full tournament list (completed, with standings) for the tournament page + their icons
    tourn_list = []
    for t in tourns:
        if not t.get("table"):
            continue
        names.add(t["name"])
        tourn_list.append({"name": t["name"], "major": 1 if _isMaj(t["name"]) else 0,
                           "table": t["table"],
                           "swiss": t.get("swiss") or [], "bracket": t.get("bracket") or [],
                           "pstats": t.get("pstats") or [],
                           "mvp": (D.get("tourn_mvp") or {}).get(t["name"], "")})
    icons = {}
    for nm in names:
        ic = tournament_icon(base_of.get(nm, nm))    # icon file is keyed by the base name
        if ic:
            icons[nm] = ic
    return team_won, icons, tourn_list

# ---------------------------------------------------------------------------
# Career archive — persistent history that is NEVER deleted.
# The game clears finished tournaments (and thus derived MVP/EVP/trophies) when a
# season rolls over. We snapshot every finished tournament — with its standings,
# the winner's roster and the MVP/EVP attribution captured while it is still
# available — into career_archive.json in the plugin folder. Every build merges
# the current finished tournaments in (deduped by a content hash so nothing is
# ever overwritten or double-counted) and then feeds the FULL accumulated history
# back into the dashboard, so tournaments, awards and trophies survive forever.
def _archive_path():
    """One archive file PER career/save slot, so different careers never mix."""
    try:
        sid = os.path.basename(os.path.normpath(latest_save()))
    except Exception:
        sid = "default"
    if not sid:
        sid = "default"
    return os.path.join(HERE, "career_archive_%s.json" % sid)

def load_archive():
    try:
        with io.open(_archive_path(), encoding="utf-8") as f:
            a = json.load(f)
        if not isinstance(a, dict):
            a = {}
    except Exception:
        a = {}
    a.setdefault("tournaments", {})
    a.setdefault("pstats", {})
    a.setdefault("seq", 0)
    return a

def save_archive(a):
    try:
        p = _archive_path()
        tmp = p + ".new"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        log("archive save err: %s" % e)

def _tourn_key(t, winner):
    """Key a tournament by name + WINNER. Same event captured many times in one
    season keeps the same winner -> one record. But when a NEW season re-runs an
    event and a DIFFERENT team wins it, that's a new title, so the new champion is
    actually credited instead of being blocked by last season's winner."""
    sig = (t.get("name", "") or "") + "|" + (winner or "")
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]

def _winner_detail(D, t):
    st = t.get("standings") or {}
    winner = next((tm for tm, pl in st.items() if pl == 1), None)
    def top(team, k):
        pls = [p for p in D["players"].values() if p.get("team") == team and p.get("nick")]
        pls.sort(key=lambda p: p.get("overall", 0), reverse=True)
        return pls[:k]
    # accurate trophy holders: players who hold this event in their honours AND whose
    # team actually finished 1st (same rule the game/compute_trophies uses)
    roster = []
    g = t.get("guid")
    if g:
        roster = [nk for nk, p in D["players"].items()
                  if g in (p.get("tournGuids") or []) and st.get(p.get("team")) == 1]
    wp = top(winner, 5) if winner else []
    if not roster:
        roster = [p["nick"] for p in wp]   # fallback: the winning team's top-5
    mvp = wp[0]["nick"] if wp else None
    evp = [p["nick"] for p in wp[1:3]]
    runner = next((tm for tm, pl in st.items() if pl == 2), None)
    if runner:
        rp = top(runner, 1)
        if rp:
            evp.append(rp[0]["nick"])
    return winner, roster, mvp, evp

def merge_archive(D):
    """Merge current finished tournaments into the persistent archive, then rebuild
    D['tournaments'] + archived awards/won from the FULL accumulated history."""
    arch = load_archive()
    # One-time rebuild: the tournament source moved from the buggy root[1] news feed
    # to the authoritative game catalog (root[51][16]). Old archived standings are
    # wrong (missing winners, everyone placed 3rd), so wipe the tournament history
    # once and let it re-archive from the correct source below. Career stat totals
    # (pstats) and value/rating history (vhist) are kept untouched.
    if arch.get("src") != "root51_v2":
        arch["tournaments"] = {}
        arch["seq"] = 0
        arch["src"] = "root51_v2"
    td = arch["tournaments"]
    seq = arch.get("seq", 0)
    # SELF-HEAL GUID NAMES: the game keys a repeated (new-season) event under a raw
    # GUID at field[0] but keeps its real name at [1]/[21]. Records captured before that
    # was resolved sit in the archive under the GUID. Every build we (a) accumulate a
    # persistent guid->real-name map from the live DataTournament and (b) rewrite any
    # GUID-named record to its real name. This runs BEFORE the dedup below, so the
    # healed record collapses into the freshly-parsed real-named one (no duplicate, and
    # a raw GUID can never reach the dashboard). Persisted so it resolves even after the
    # event scrolls out of the DataTournament files.
    gn = arch.setdefault("guid_names", {})
    try:
        for _k, _v in load_datatournament().items():
            if isinstance(_k, str) and _is_guid_nick(_k):
                _rn = S(at(A(_v), 1)) or S(at(A(_v), 21)) or ""
                if _rn and not _is_guid_nick(_rn):
                    gn[_k] = _rn
        for _r in td.values():
            _nm = _r.get("name")
            if isinstance(_nm, str) and _is_guid_nick(_nm) and gn.get(_nm):
                _r["name"] = gn[_nm]
    except Exception as _e:
        log("guid-name heal: %s" % _e)
    # Dedup SEMANTICALLY by (name, winner) — not by storage key. This keeps every
    # distinct title (incl. the same event won by different teams in different
    # seasons) while collapsing repeat captures of the same win. Works regardless of
    # whether old records were stored under the legacy name-only key.
    seen = {}
    for k in list(td.keys()):
        r = td[k]
        dk = (r.get("name"), r.get("winner"))
        if dk in seen:
            if not td[seen[dk]].get("winner") and r.get("winner"):
                del td[seen[dk]]; seen[dk] = k
            else:
                del td[k]
        else:
            seen[dk] = k
    try:
        majors = load_tournament_majors()
    except Exception:
        majors = set()
    for t in D.get("tournaments", []):
        if not t.get("table"):
            continue
        winner, roster, mvp, evp = _winner_detail(D, t)
        dk = (t["name"], winner)
        if dk in seen:                         # this exact title already archived
            continue
        seq += 1
        key = _tourn_key(t, winner)
        while key in td:                       # guarantee a unique storage slot
            key += "x"
        td[key] = {"name": t["name"], "guid": t.get("guid", ""), "tier": t.get("tier", 0),
                   "major": 1 if t["name"] in majors else 0,
                   "standings": t["standings"], "table": t["table"],
                   "swiss": t.get("swiss") or [], "bracket": t.get("bracket") or [],
                   "winner": winner, "roster": roster, "mvp": mvp, "evp": evp, "seq": seq}
        seen[dk] = key
    arch["seq"] = seq

    # ---- career stat accumulation (survives season resets) ----
    # Per-season aggregates reset when the game clears MapStats. We keep a running
    # career total per player: whenever the current map count DROPS (season/team
    # reset), we bank the previous peak into "career" and start counting again.
    pst = arch.setdefault("pstats", {})
    for nk, p in D["players"].items():
        st = p.get("stats")
        if not st or "raw" not in st:
            continue
        cur = list(st["raw"])                    # [maps,k,d,a,dmg,mvp,rounds]
        rec = pst.get(nk)
        if rec is None:
            pst[nk] = {"career": [0, 0, 0, 0, 0, 0, 0], "last": cur}
        else:
            last = rec.get("last") or [0] * 7
            if len(last) >= 1 and cur[0] < last[0] - 1:   # real drop -> season reset
                car = rec.get("career") or [0] * 7
                rec["career"] = [c + l for c, l in zip(car, last)]
            rec["last"] = cur
    save_archive(arch)

    # attach career totals (career banked + current season) onto each player
    for nk, p in D["players"].items():
        rec = pst.get(nk)
        if not rec:
            continue
        tot = [c + l for c, l in zip(rec.get("career", [0] * 7), rec.get("last", [0] * 7))]
        maps, k, d, a, dmg, mvp, rnds = tot
        if maps <= 0:
            continue
        dd = max(1, d); rr = max(1, rnds)
        p["career"] = {"maps": maps, "k": k, "d": d, "a": a, "mvp": mvp,
                       "kd": round(k / dd, 2), "adr": round(dmg / rr, 1), "kpr": round(k / rr, 2),
                       "rating": round(0.45 + 0.55 * (k / dd) * (dmg / rr / 78.0), 2)}

    recs = sorted(td.values(), key=lambda r: r.get("seq", 0))
    # SEASON SEPARATION: when an event is run again in a later season it becomes a
    # SECOND record here. Give each repeat a distinct display name ("… (сезон N)")
    # so both seasons show as their own page with their own bracket/winner/MVP —
    # the old one is never overwritten and the new one is never hidden.
    _grp = {}
    for r in recs:
        _grp.setdefault(r.get("name"), []).append(r)
    _idn = {}
    _latest_disp = {}                       # base name -> newest instance's display name
    for r in recs:
        base = r.get("name")
        grp = _grp[base]
        if len(grp) <= 1:
            dn = base
        else:
            dn = "%s (сезон %d)" % (base, grp.index(r) + 1)
        _idn[id(r)] = dn
        _latest_disp[base] = dn             # recs are seq-sorted, so last wins = newest
    def _dn(r):
        return _idn.get(id(r), r.get("name"))
    D["tourn_latest_disp"] = _latest_disp    # let the calendar point at the newest instance
    # per-player tournament stats live only in the CURRENT save (not archived), so grab
    # them from the live parse (D['tournaments'] right now) and re-attach by base name.
    _live_ps = {t.get("name"): t.get("pstats") for t in (D.get("tournaments") or []) if t.get("pstats")}
    D["tournaments"] = [{"name": _dn(r), "base": r.get("name"), "guid": r.get("guid", ""),
                         "tier": r.get("tier", 0), "standings": r["standings"], "table": r["table"],
                         "swiss": r.get("swiss") or [], "bracket": r.get("bracket") or [],
                         "pstats": _live_ps.get(r.get("name")) or []}
                        for r in recs]
    # Build the MVP map + MVP awards + trophies straight from the archive records
    # (the complete, per-season source). Keyed by DISPLAY name so each season keeps
    # its own MVP/champion; the Major check uses the BASE name.
    D["tourn_mvp"] = {}
    awards, won, team_won = {}, {}, {}
    for r in recs:
        dn = _dn(r); base = r.get("name")
        mv = r.get("mvp")
        if mv:
            D["tourn_mvp"][dn] = mv
            a = awards.setdefault(mv, {"mvp": 0, "evp": 0, "mvpEvents": [], "evpEvents": [],
                                       "majorMvp": 0, "majorMvpEvents": []})
            a["mvp"] += 1; a["mvpEvents"].append(dn)
            if base in majors:                # MVP of a Major (per the game's tier)
                a["majorMvp"] += 1; a["majorMvpEvents"].append(dn)
        mj = 1 if base in majors else 0
        if r.get("winner"):
            team_won.setdefault(r["winner"], []).append((dn, mj))
        for nk in (r.get("roster") or []):
            won.setdefault(nk, []).append((dn, mj))
    D["archived_awards"] = awards
    D["archived_won"] = won
    D["archived_team_won"] = team_won

    # ---- world Top-20 of the year — the GAME'S OWN ranking from EMTV news ----
    # Read straight from the game's year-end Top-20 emails (not a dashboard re-rank),
    # archived per year so it survives even if the player deletes the emails. Builds
    # each player's placement history ("#16 in 2026, #1 in 2027, ...") forever.
    t20 = arch.setdefault("top20_by_year", {})     # {year -> {rank(str) -> nick}}
    for yr, ranks in (D.get("game_top20") or {}).items():
        cur = t20.get(str(yr))
        cur = cur if isinstance(cur, dict) else {}  # drop any old rating-based list format
        for rk, nk in ranks.items():
            if nk:
                cur[str(rk)] = nk                  # game data wins; fills/updates the year
        if cur:
            t20[str(yr)] = cur
    hist20 = {}
    for y, ranks in t20.items():
        for rk, nk in (ranks.items() if isinstance(ranks, dict) else enumerate(ranks, 1)):
            try:
                hist20.setdefault(nk, []).append([int(y), int(rk)])
            except Exception:
                pass
    for nk in hist20:
        hist20[nk].sort()
    D["top20_history"] = hist20
    # full year->rank->nick table, so the dashboard can open a whole year's Top-20 grid
    D["top20_by_year"] = {str(y): (r if isinstance(r, dict) else {str(i + 1): n for i, n in enumerate(r)})
                          for y, r in t20.items()}

    # ---- value + rating history (for the over-time chart) ----
    vh = arch.setdefault("vhist", {})
    vs = arch.get("vseq", 0) + 1
    arch["vseq"] = vs
    for nk, p in D["players"].items():
        val = p.get("value", 0)
        st = p.get("stats") or {}
        rat = st.get("rating", 0) or 0
        if not val and not rat:
            continue
        h = vh.setdefault(nk, [])
        if (not h) or h[-1][1] != val or abs((h[-1][2] or 0) - rat) > 0.02:
            h.append([vs, val, rat])
            if len(h) > 80:
                del h[:len(h) - 80]

    # ---- Hall of Fame: snapshot every retired player's final card, kept forever ----
    hof = arch.setdefault("hof", {})
    for nk, p in D["players"].items():
        if not p.get("retired"):
            continue
        aw = awards.get(nk) or {}
        hof[nk] = {"nick": nk, "first": p.get("first", ""), "last": p.get("last", ""),
                   "country": p.get("country", ""), "age": p.get("age"),
                   "overall": p.get("overall", 0), "potential": p.get("potential", 0),
                   "role": role_single(p) if p.get("attrs") else "",
                   "career": p.get("career"), "mvp": aw.get("mvp", 0), "evp": aw.get("evp", 0),
                   "won": won.get(nk, []), "attrs": p.get("attrs", {})}
    save_archive(arch)
    D["hof"] = hof
    D["vhist"] = vh
    log("archive: %d tournaments, %d in HoF" % (len(td), len(hof)))
    return arch

def load_game_logos():
    # crests extracted straight from the game's Unity assets (UnityPy), keyed by team nick.
    # Produced once by extract_logos and cached to game_logos.json in the plugin folder.
    p = os.path.join(HERE, "game_logos.json")
    try:
        if os.path.isfile(p):
            return json.load(io.open(p, encoding="utf-8"))
    except Exception as e:
        log("game_logos load fail: %s" % e)
    return {}

def build_all_logos(teams, teamFull, idx, resS_path):
    out = {}
    for nm in teams:
        try:
            b = logo_small(nm, teamFull.get(nm, ""), idx, resS_path)
            if b: out[nm] = b
        except Exception as e:
            log("all logo fail %s: %s" % (nm, e))
    return out

# ---------------- main ----------------
def latest_save():
    root = os.path.join(LOW_DIR, "Save")
    best, bt = None, -1
    if not os.path.isdir(root): return None
    for d in os.listdir(root):
        f = os.path.join(root, d, "SlotData.mpack")
        if os.path.isfile(f):
            t = os.path.getmtime(f)
            if t > bt: bt, best = t, os.path.join(root, d)
    return best

def build_ranking(D, team_won=None):
    # HLTV-style world ranking: every team by its stored rank, with a top-5 roster
    team_won = team_won or {}
    rosters = {}
    tval = {}   # team -> total squad market value
    for p in D["players"].values():
        t = p["team"]
        if not t: continue
        rosters.setdefault(t, []).append((p["overall"], p["nick"], p.get("country", ""), p.get("age"),
                                          p.get("first", ""), p.get("last", "")))
        tval[t] = tval.get(t, 0) + p.get("value", 0)
    tmed = D.get("teamMedalsAll", {})
    tearn = D.get("team_earnings", {})
    phist = D.get("pointsHist", {})
    rhist = D.get("rankHist", {})
    out = []
    for nm, rk in D["teamRank"].items():
        full_roster = sorted(rosters.get(nm, []), key=lambda x: x[0], reverse=True)
        rl = full_roster[:6]
        top5 = [x[0] for x in full_roster[:5]]
        avg_ovr = round(sum(top5) / len(top5), 1) if top5 else 0
        pv = phist.get(nm)
        cur_pts = int(round(pv[-1])) if pv else 0    # the game's own ranking points (raw)
        rv = rhist.get(nm)
        move = (rv[-2] - rv[-1]) if (rv and len(rv) >= 2) else 0   # +ve = climbed
        out.append({"rank": rk, "team": nm, "full": D["teamFull"].get(nm, nm),
                    "country": D["teamCountry"].get(nm, ""),
                    "medals": tmed.get(nm, [0, 0, 0]),
                    "avg_ovr": avg_ovr, "sq_value": tval.get(nm, 0), "roster_size": len(full_roster),
                    "earnings": tearn.get(nm, 0), "points": cur_pts, "move": move,
                    "won": [{"n": n, "m": 1 if mj else 0} for n, mj in team_won.get(nm, [])],
                    "roster": [{"nick": x[1], "overall": x[0], "country": x[2], "age": x[3],
                                "first": x[4], "last": x[5]} for x in rl]})
    out.sort(key=lambda x: x["rank"])
    return out

# ---- role inference (roles aren't stored explicitly; derive from attributes) ----
# attr indices: 7=Entry, 8=Support, 9=Sniping(AWP), 14=Leadership(IGL)
ROLE_ATTR = {"AWP": "9", "IGL": "14", "Entry": "7", "Support": "8"}

def _attr(p, key):
    try:
        return float((p.get("attrs") or {}).get(key, 0) or 0)
    except Exception:
        return 0.0

def assign_team_roles(players):
    """Give each of a team's players a role: one best-fit AWP/IGL/Entry/Support,
    everyone else a Rifler. Mutates each dict with p['role']."""
    for p in players:
        p["role"] = "Rifler"
    pool = list(players)
    for role in ("AWP", "IGL", "Entry", "Support"):
        if not pool:
            break
        ai = ROLE_ATTR[role]
        cand = max(pool, key=lambda p: _attr(p, ai))
        if _attr(cand, ai) > 0:
            cand["role"] = role
            pool.remove(cand)

def role_single(p):
    """Best-guess role for an individual (free agent) from their own attributes."""
    scores = {r: _attr(p, ROLE_ATTR[r]) for r in ("AWP", "IGL", "Entry", "Support")}
    best = max(scores, key=scores.get)
    attrs = [float(v) for v in (p.get("attrs") or {}).values()]
    mean = sum(attrs) / len(attrs) if attrs else 0
    if scores[best] <= 0 or scores[best] < mean + 0.5:
        return "Rifler"
    return best

TIER_NAME = {0: "Tier 1", 1: "Tier 2", 2: "Major"}

def catalog_base(root):
    """root[51] = full tournament catalog. Return the significant events (Majors,
    Tier-1, and larger Tier-2) with tier, prizefund, host city/country, prestige.
    field[4]=tier(0=T1,1=lower,2=Major), [5]=prestige, [6]=prize, [9]=country, [10]=city."""
    out = []
    _MAJSET = load_tournament_majors()
    for e in full_catalog(root):
        a = A(e)
        if not a or len(a) < 11 or not isinstance(a[0], str):
            continue
        name = a[0]; tier = L(at(a, 4)); prize = L(at(a, 6))
        if _is_guid_nick(name):               # skip unnamed placeholder/next-season slots
            continue
        # drop tiny UNFINISHED filler events, but KEEP any event that already has a
        # real result so the user's smaller/regional tournaments still show up.
        has_result = bool(A(at(a, 16))) or bool(M(at(a, 23)))
        if tier == 1 and prize < 300000 and not has_result:
            continue
        # Major status comes from the SAME authoritative source as trophies/honours
        # (the DB Tier column via load_tournament_majors) so the calendar badge can never
        # disagree with the team/player Major badges. A DB-demoted "... Major" (Budapest)
        # therefore shows as a normal top-tier event here too.
        is_major = name in _MAJSET
        out.append({"name": name, "tier": tier, "major": 1 if is_major else 0,
                    "tierName": "Major" if is_major else ("Tier 1" if tier == 0 else "Tier 2"),
                    "rating": round(Dd(at(a, 5)), 2), "prize": prize,
                    "city": S(at(a, 10)) or "", "country": S(at(a, 9)) or ""})
    return out

def _apply_display_nicks(payload, disp):
    """The save stores two names per player: an internal key at field[0]
    ("huNter_kovac", "PR_nový", "Broland") and the real DISPLAY nick at field[1]
    ("huNter", "PR", "Brollan"). Everything upstream is keyed by the internal key
    (photos, stats, awards) so it keeps working; here, on the FINISHED payload, we
    swap the SHOWN name to the real nick, re-key the players dict, and alias photos
    so the client (which is unchanged) resolves images under the real nick too."""
    if not disp:
        return payload
    def walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if k in ("nick", "mvp") and isinstance(v, str) and v in disp:
                    o[k] = disp[v]
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for it in o:
                if isinstance(it, (dict, list)):
                    walk(it)
    # don't waste time walking the big media dicts (no player nicks live there)
    for key, val in payload.items():
        if key in ("photos", "flags", "logos", "disp"):
            continue
        walk(val)
    # re-key the players dict by the real display nick
    pl = payload.get("players")
    if isinstance(pl, dict):
        newpl = {}
        for k, o in pl.items():
            nk = o.get("nick", k) if isinstance(o, dict) else k
            newpl[nk] = o
        payload["players"] = newpl
    # alias photos so the (now display) nick resolves to the same image
    ph = payload.get("photos")
    if isinstance(ph, dict):
        for key, d in disp.items():
            if key in ph and d not in ph:
                ph[d] = ph[key]
    return payload

# Tournaments the user has renamed for the dashboard. Applied as the very LAST step of
# the build (after every key/dedup/season-suffix decision is already made), so relabelling
# an event can never disturb the career archive, trophy dedup or winner attribution — it
# only changes the SHOWN name. Substring match keeps any "(сезон N)" suffix intact.
_TOURN_RENAME = {"StarLadder Budapest Major": "StarLadder Budapest Series"}

def _rn_tourn(nm):
    if not isinstance(nm, str):
        return nm
    for old, new in _TOURN_RENAME.items():
        if old in nm:
            nm = nm.replace(old, new)
    return nm

def _apply_tournament_renames(payload):
    if not _TOURN_RENAME:
        return payload
    def fix_won(obj):
        lst = obj.get("won") if isinstance(obj, dict) else None
        if not isinstance(lst, list):
            return
        new = []
        for w in lst:
            if isinstance(w, dict):
                if isinstance(w.get("n"), str):
                    w["n"] = _rn_tourn(w["n"])
                new.append(w)
            elif isinstance(w, (list, tuple)) and w and isinstance(w[0], str):
                new.append([_rn_tourn(w[0])] + list(w[1:]))   # hof [name, major] pairs
            else:
                new.append(w)
        obj["won"] = new
    for c in payload.get("calendar") or []:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            c["name"] = _rn_tourn(c["name"])
    for t in payload.get("tournaments") or []:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            t["name"] = _rn_tourn(t["name"])
    fix_won(payload.get("team") or {})
    for p in (payload.get("players") or {}).values():
        if not isinstance(p, dict):
            continue
        fix_won(p)
        for k in ("mvpEvents", "evpEvents", "majorMvpEvents"):
            if isinstance(p.get(k), list):
                p[k] = [_rn_tourn(x) for x in p[k]]
    for h in payload.get("hof") or []:
        fix_won(h)
    for r in payload.get("ranking") or []:
        fix_won(r)
    # alias tournament icons so the NEW display name still resolves to a logo
    ic = payload.get("tourn_icons")
    if isinstance(ic, dict):
        for k in list(ic.keys()):
            nk = _rn_tourn(k)
            if nk != k and nk not in ic:
                ic[nk] = ic[k]
    return payload

def build_payload(D, photos, team_logo, tlogos):
    my = D["myTeam"]
    # bulk extra photo URLs {nick: url}. Merged HERE (not in run()) because the
    # localhost server builds photos in its own build() and only shares build_payload.
    # These OVERRIDE the DB (so a dead DB url like picui.cn is replaced by a working one).
    try:
        _ep = os.path.join(HERE, "extra_photos.json")
        if os.path.isfile(_ep):
            _cpa = custom_photos_all()
            for _nk, _url in json.load(io.open(_ep, encoding="utf-8")).items():
                if _url and _nk not in _cpa:
                    photos[_nk] = _url
    except Exception as _e:
        log("extra_photos(bp): %s" % _e)
    # accumulate the full career history first (never deletes past seasons)
    try:
        merge_archive(D)
    except Exception as e:
        log("merge_archive err: %s" % e)
    # prize money earned per team (sum of final-placement prize across all tournaments)
    team_earnings = {}
    for t in D.get("tournaments", []):
        for row in t.get("table", []):
            if row.get("prize"):
                team_earnings[row["team"]] = team_earnings.get(row["team"], 0) + row["prize"]
    D["team_earnings"] = team_earnings
    team_count = {}
    for p in D["players"].values():
        if p.get("team"):
            team_count[p["team"]] = team_count.get(p["team"], 0) + 1
    team_won, tourn_icons, tourn_list = compute_trophies(D)   # sets p["won"]
    # override player trophies with the persistent archive so old seasons never vanish
    for nk, wl in (D.get("archived_won") or {}).items():
        p = D["players"].get(nk)
        if not p:
            continue
        cur = p.get("won") or []
        seen = set(n for n, _ in cur)
        for n, m in wl:
            if n not in seen:
                cur.append((n, m)); seen.add(n)
        p["won"] = cur
    for tm, wl in (D.get("archived_team_won") or {}).items():
        cur = team_won.get(tm) or []
        seen = set(n for n, _ in cur)
        for n, m in wl:
            if n not in seen:
                cur.append((n, m)); seen.add(n)
        team_won[tm] = cur
    # MVP/EVP from the full archived history (captured with correct rosters at the time)
    awards = D.get("archived_awards") or compute_tournament_awards(D)
    hist = D.get("ratingHist", {})
    assign_team_roles(D["roster"])                            # sets p["role"] on roster
    # tournament calendar: enrich the catalog with completed/upcoming + winner
    winners = {}
    for t in D.get("tournaments", []):
        for tm, pl in t.get("standings", {}).items():
            if pl == 1:
                winners[t["name"]] = tm
    done_names = set(t["name"] for t in D.get("tournaments", []) if t.get("table"))
    latest = D.get("tourn_latest_disp", {})     # base name -> newest season's display name
    calendar = []
    for c in D.get("catalog", []):
        dn = latest.get(c["name"], c["name"])   # a repeated event resolves to its NEWEST run
        cc = dict(c); cc["name"] = dn
        cc["done"] = 1 if dn in done_names else 0
        cc["winner"] = winners.get(dn, "")
        calendar.append(cc)
    calendar.sort(key=lambda t: (-(2 if t["tier"] == 2 else (1 if t["tier"] == 0 else 0)), -t["prize"]))
    # tournament logos for the calendar (CustomAssets/Tournaments/<name>.png)
    for c in calendar:
        nm = c["name"]
        if nm not in tourn_icons:
            ic = tournament_icon(nm)
            if ic:
                tourn_icons[nm] = ic
    # only players shown anywhere (roster + anyone with match stats); drops ~5000
    # never-referenced players, shrinking the page and speeding up json/write
    keep = set(nk for nk, p in D["players"].items() if p.get("stats")) | set(p["nick"] for p in D["roster"])
    # tournament -> winning team (live standings + full archive), for trophy-by-team grouping
    tourn_winner = dict(winners)
    for tm, wl in (D.get("archived_team_won") or {}).items():
        for n, _mj in wl:
            tourn_winner.setdefault(n, tm)
    club_hist = build_club_history(D.get("transfers") or [], D["players"], tourn_winner)
    players = {}
    for nk, p in D["players"].items():
        if nk not in keep: continue
        o = {"nick": p["nick"], "first": p["first"], "last": p["last"], "country": p["country"],
             "team": p["team"], "overall": p["overall"], "potential": p["potential"],
             "value": p.get("value", 0), "age": p["age"], "attrs": p["attrs"]}
        if p.get("role"): o["role"] = p["role"]
        aw = awards.get(nk)
        if aw:
            if aw["mvp"]: o["mvp"] = aw["mvp"]   # tournament MVP count
            if aw["evp"]: o["evp"] = aw["evp"]   # tournament EVP count
            if aw.get("mvpEvents"): o["mvpEvents"] = aw["mvpEvents"]   # which events
            if aw.get("evpEvents"): o["evpEvents"] = aw["evpEvents"]
            if aw.get("majorMvp"): o["majorMvp"] = aw["majorMvp"]      # MVP-of-a-Major count
            if aw.get("majorMvpEvents"): o["majorMvpEvents"] = aw["majorMvpEvents"]
        th = (D.get("top20_history") or {}).get(nk)
        if th: o["top20hist"] = th               # [[year, rank], ...] world Top-20 finishes
        if p.get("stats"):
            o["stats"] = {k2: v2 for k2, v2 in p["stats"].items() if k2 != "raw"}   # drop bulky raw
        if p.get("career"): o["career"] = p["career"]                                # all-time totals
        if p.get("retired"): o["retired"] = 1
        vhh = (D.get("vhist") or {}).get(nk)
        if vhh and len(vhh) > 1: o["vhist"] = vhh                                     # value/rating over time
        pe = D.get("player_earnings", {}).get(nk, 0)   # real personal career prize (transfer-stable)
        if pe:
            o["earnings"] = pe
        if p.get("medals"): o["medals"] = p["medals"]
        if club_hist.get(nk): o["clubs"] = club_hist[nk]        # club path oldest -> current
        if p.get("won"): o["won"] = [{"n": n, "m": 1 if mj else 0} for n, mj in p["won"]]
        h = hist.get(nk)
        if h and len(h) >= 2: o["hist"] = h[-24:]
        players[nk] = o
    # free agents (unsigned players) for the market page — top by overall, keep it light
    fa = sorted([p for p in D["players"].values() if not p.get("team")],
                key=lambda p: p.get("overall", 0), reverse=True)[:150]
    free_agents = [{"nick": p["nick"], "overall": p.get("overall", 0),
                    "country": p.get("country", ""), "age": p.get("age"),
                    "value": p.get("value", 0), "role": role_single(p),
                    "first": p.get("first", ""), "last": p.get("last", "")} for p in fa]
    # young high-potential players ("wonderkids") — young + big growth headroom
    tal = [p for p in D["players"].values()
           if p.get("age") and p["age"] <= 21 and (p.get("potential", 0) - p.get("overall", 0)) >= 3]
    tal.sort(key=lambda p: (p.get("potential", 0), p.get("potential", 0) - p.get("overall", 0)), reverse=True)
    talents = [{"nick": p["nick"], "first": p.get("first", ""), "last": p.get("last", ""),
                "country": p.get("country", ""), "team": p.get("team", ""), "age": p["age"],
                "overall": p.get("overall", 0), "potential": p.get("potential", 0),
                "value": p.get("value", 0), "role": role_single(p)} for p in tal[:150]]
    # best value-for-money buys: a strong overall for a low market value. Each $15k of
    # value "costs" one overall point, so cheap-but-good players float to the top.
    # only real transfer targets: players currently ON a team (teamless free agents
    # carry an unreliable overall and aren't a "buy" in the same sense)
    brg = [p for p in D["players"].values()
           if p.get("value", 0) > 0 and p.get("overall", 0) >= 55 and p.get("team")]
    brg.sort(key=lambda p: p.get("overall", 0) - p.get("value", 0) / 15000.0, reverse=True)
    bargains = [{"nick": p["nick"], "first": p.get("first", ""), "last": p.get("last", ""),
                 "country": p.get("country", ""), "team": p.get("team", ""), "age": p.get("age"),
                 "overall": p.get("overall", 0), "potential": p.get("potential", 0),
                 "value": p.get("value", 0), "role": role_single(p)} for p in brg[:60]]
    # most valuable players in the world (by market value)
    tv = sorted(D["players"].values(), key=lambda p: p.get("value", 0), reverse=True)
    top_value = [{"nick": p["nick"], "first": p.get("first", ""), "last": p.get("last", ""),
                  "country": p.get("country", ""), "team": p.get("team", ""), "age": p.get("age"),
                  "overall": p.get("overall", 0), "value": p.get("value", 0)}
                 for p in tv if p.get("value", 0) > 0][:30]
    # --- Top-20 players + Team of Season (best by rating this season) ---
    elig = [p for p in D["players"].values() if p.get("stats") and p["stats"].get("maps", 0) > 0]
    top20 = []
    if elig:
        mx = max(p["stats"]["maps"] for p in elig)
        mn = max(5, int(mx * 0.4))
        pool = [p for p in elig if p["stats"]["maps"] >= mn] or elig
        pool = sorted(pool, key=lambda p: p["stats"]["rating"], reverse=True)[:20]
        aw = D.get("archived_awards", {})
        for i, p in enumerate(pool):
            c = p.get("career") or {}
            top20.append({"rank": i + 1, "nick": p["nick"], "first": p.get("first", ""),
                          "last": p.get("last", ""), "country": p.get("country", ""),
                          "team": p.get("team", ""), "overall": p.get("overall", 0),
                          "role": role_single(p), "maps": p["stats"]["maps"], "kd": p["stats"]["kd"],
                          "adr": p["stats"]["adr"], "rating": p["stats"]["rating"],
                          "cRating": c.get("rating"), "cMaps": c.get("maps"),
                          "mvp": (aw.get(p["nick"], {}) or {}).get("mvp", 0)})
    team_of_season = top20[:5]
    # --- Hall of Fame (retired players, preserved forever) ---
    hof_list = list((D.get("hof") or {}).values())
    hof_list.sort(key=lambda h: (len(h.get("won") or []), h.get("mvp", 0),
                                 (h.get("career") or {}).get("maps", 0), h.get("overall", 0)), reverse=True)
    hof_list = hof_list[:80]
    payload = {
        "my_team": my,
        "top20": top20,
        "team_of_season": team_of_season,
        "hof": hof_list,
        "team": {"full": D["teamFull"].get(my, my), "country": D["teamCountry"].get(my, ""),
                 "rank": D["teamRank"].get(my, 0),
                 "won": [{"n": n, "m": 1 if mj else 0} for n, mj in team_won.get(my, [])]},
        "team_medals": D["team_medals"],
        "tourn_icons": tourn_icons,
        "avg_age": D["avgAge"],
        "roster_nicks": [p["nick"] for p in D["roster"]],
        "coach": ({"nick": D["coach"]["nick"], "first": D["coach"]["first"], "last": D["coach"]["last"]} if D["coach"] else None),
        "staff": [{"nick": s["nick"], "first": s["first"], "last": s["last"], "role": s["role"], "skill": s["skill"]} for s in D["staff"]],
        "money": {"cash": D["cash"]},
        "txns": [{"desc": t[0], "day": t[1], "month": t[2], "amt": t[3]} for t in D["txns"]],
        "trophies": D["trophies"],
        "players": players,
        "scoreboards": D["scoreboards"],
        "my_matches": [],
        "ranking": build_ranking(D, team_won),
        "rank_hist": D.get("rankHist", {}),
        "transfers": D.get("transfers", [])[:5000],
        "free_agents": free_agents,
        "talents": talents,
        "top_value": top_value,
        "bargains": bargains,
        "tournaments": tourn_list,
        "top20_by_year": D.get("top20_by_year", {}),
        "calendar": calendar,
        "photos": photos,
        "logos": _merge_logos(my, team_logo, tlogos),
        "flags": load_flags(),
        "disp": D.get("disp", {}),   # {internal key -> real display nick} for the 131 dual-named players
    }
    return _apply_tournament_renames(_apply_display_nicks(payload, D.get("disp")))

def _merge_logos(my, team_logo, tlogos):
    logos = dict(tlogos) if tlogos else {}
    if team_logo:
        logos[my] = team_logo
    return logos

def run():
    log("=== run %s ===" % __file__)
    save_dir = latest_save()
    if not save_dir:
        raise RuntimeError("No save found in " + os.path.join(LOW_DIR, "Save"))
    log("save: " + save_dir)
    root = MP(open(os.path.join(save_dir, "SlotData.mpack"), "rb").read()).parse()
    D = extract(A(root) or [])
    log("team: %s  roster: %d  players: %d" % (D["myTeam"], len(D["roster"]), len(D["players"])))
    aggregate(D, save_dir)

    import mmap
    res_path = os.path.join(DATA_DIR, "resources.assets")
    resS_path = res_path + ".resS"
    sig = _sig(res_path)
    idx = load_cache(CACHE_TEX, sig)
    if idx is None:
        with open(res_path, "rb") as rf:
            mm = mmap.mmap(rf.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                idx = build_tex_index(mm, mm.size())
            finally:
                mm.close()
        save_cache(CACHE_TEX, sig, idx)
        log("textures: %d (built)" % len(idx))
    else:
        log("textures: %d (cached)" % len(idx))

    dbP, dbL = load_emdb()             # photo/logo URLs straight from the roster database
    log("emdb: %d player photos, %d team logos" % (len(dbP), len(dbL)))
    need = set(p["nick"] for p in D["roster"])
    for p in D["players"].values():
        if p.get("stats"): need.add(p["nick"])
    pcache = load_cache(CACHE_PHOTO, sig) or {}
    photos = dict(dbP)                 # every player that has a URL in the DB
    new = 0
    for nk in need:
        cp = custom_photo(nk)          # user overrides are always read fresh (highest priority)
        if cp:
            photos[nk] = cp; continue
        if nk in photos: continue      # already have a DB photo URL
        b64 = pcache.get(nk)
        if b64 is None:                # "" is cached for "no photo" so we don't retry
            pp = D["players"].get(nk, {})
            b64 = extract_photo(idx, resS_path, nk, pp.get("first", ""), pp.get("last", "")) or ""
            pcache[nk] = b64; new += 1
        if b64: photos[nk] = b64
    cpa = custom_photos_all()
    for nk, b in cpa.items():                   # user custom image FILES override everything
        photos[nk] = b
    # extra photo URLs {nick: url} added in bulk (e.g. from the web) WITHOUT touching
    # the encrypted DB. Browser loads the URL directly. A custom file still wins.
    try:
        ep = os.path.join(HERE, "extra_photos.json")
        if os.path.isfile(ep):
            extra = json.load(io.open(ep, encoding="utf-8"))
            nadd = 0
            for nk, url in extra.items():
                if url and nk not in cpa:
                    photos[nk] = url; nadd += 1
            log("extra_photos: %d urls merged" % nadd)
    except Exception as e:
        log("extra_photos load fail: %s" % e)
    if new: save_cache(CACHE_PHOTO, sig, pcache)
    log("photos: %d / %d (%d new)" % (len(photos), len(need), new))

    # crests for EVERY ranked team (cached to disk, keyed to resources.assets sig)
    custom_logos = load_cache(CACHE_LOGOS, sig)
    if not isinstance(custom_logos, dict):
        custom_logos = build_all_logos(set(D["teamRank"].keys()), D["teamFull"], idx, resS_path)
        save_cache(CACHE_LOGOS, sig, custom_logos)
    game_logos = load_game_logos()      # extracted straight from the game (UnityPy)
    tlogos = dict(game_logos)           # game crests as the base for every team
    for tnm, url in dbL.items():        # DB logo URLs preferred (lighter than base64)
        tlogos[tnm] = url
    tlogos.update(custom_logos)         # user CustomAssets overrides everything
    log("logos: db=%d game=%d custom=%d total=%d" % (len(dbL), len(game_logos), len(custom_logos), len(tlogos)))
    # my own crest for the big header
    team_logo = custom_logos.get(D["myTeam"]) or logo_for(D["myTeam"], D["teamFull"].get(D["myTeam"], ""), idx, resS_path) or tlogos.get(D["myTeam"])

    payload = build_payload(D, photos, team_logo, tlogos)
    try:
        payload["saved_at"] = time.strftime("%d.%m %H:%M", time.localtime(
            os.path.getmtime(os.path.join(save_dir, "SlotData.mpack"))))
    except Exception:
        payload["saved_at"] = ""

    # overlay LIVE values written by the plugin from game memory on this F3 press
    try:
        lp = os.path.join(HERE, "live.json")
        if os.path.isfile(lp) and (time.time() - os.path.getmtime(lp)) < 120:
            live = json.load(io.open(lp, encoding="utf-8"))
            if live.get("balance"):
                payload["money"]["cash"] = live["balance"]
            r = (live.get("ranks") or {}).get(D["myTeam"])
            if r:
                payload["team"]["rank"] = r
            payload["live"] = True
            log("live overlay: balance=%s rank=%s" % (live.get("balance"), r))
    except Exception as e:
        log("live overlay fail: " + str(e))
    with io.open(os.path.join(HERE, "template.html"), "r", encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False)).replace("__TEAM__", D["myTeam"])
    out_html = os.path.join(LOW_DIR, "EM26_TeamCenter.html")
    with io.open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    log("wrote: " + out_html)
    try:
        os.startfile(out_html)
    except Exception:
        webbrowser.open("file:///" + out_html.replace("\\", "/"))

if __name__ == "__main__":
    try:
        run()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        # surface the error so a silent pythonw launch is still diagnosable
        try:
            err = os.path.join(LOW_DIR, "EM26_TeamCenter_error.txt")
            with io.open(err, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)
