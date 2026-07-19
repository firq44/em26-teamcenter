# -*- coding: utf-8 -*-
# EM2026 Team Center generator — invoked by the BepInEx plugin on F3.
# Reads the current save + game assets, builds an HLTV-style dashboard for
# the team you are currently managing, and opens it in the browser.
import os, sys, io, glob, json, struct, base64, traceback, webbrowser, time, pickle, hashlib

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

def clean_nick(n):
    if not n: return ""
    if len(n) > 18: return ""
    if len(n) >= 9 and n[8] == '-': return ""
    return n

# ---------------- extract data model ----------------
def extract(root):
    D = {"myTeam": "", "players": {}, "roster": [], "teamRank": {}, "teamFull": {},
         "teamCountry": {}, "trophies": [], "team_medals": [0, 0, 0], "staff": [], "coach": None,
         "cash": 0, "txns": [], "avgAge": None, "scoreboards": [], "pointsHist": {}, "rankHist": {}}
    org = A(at(root, 22)); D["myTeam"] = S(at(org, 1)) or ""
    my = D["myTeam"]
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
    # REAL tournament MVP: the tournament database (root[51]) stores the actual MVP
    # nick in slot 18 of each record — this is exactly what the game shows.
    D["tourn_mvp"] = {}
    for rec in (A(at(root, 51)) or []):
        ra = A(rec)
        if not ra or len(ra) < 19:
            continue
        nm = S(at(ra, 0)); mvp = S(at(ra, 18))
        if nm and mvp:
            D["tourn_mvp"][nm] = mvp
    D["transfers"] = parse_transfers(root)
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
    th = max(1, int(round(h / w * tw)))
    comp = comp.resize((tw, th), Image.LANCZOS)
    out = io.BytesIO(); comp.save(out, 'JPEG', quality=82)
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

def extract_photo(idx, resS_path, nick, first="", last=""):
    for name in _photo_names(nick, first, last):
        r = _tex_rgba(idx, resS_path, name)
        if r:
            try:
                return rgba_to_jpg_b64(r[0], r[1], r[2], 190)
            except Exception as e:
                log("jpg fail %s: %s" % (name, e))
    return None

def extract_photo_raw(idx, resS_path, nick, first="", last="", tw=256):
    # raw JPEG bytes for the on-demand /photo endpoint (any player, not just roster)
    from PIL import Image
    for name in _photo_names(nick, first, last):
        r = _tex_rgba(idx, resS_path, name)
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

def logo_for(team, full, idx, resS_path):
    # custom logos are named by short name OR full name (e.g. "VyaliePitony.png")
    for nm in (team, full):
        if not nm: continue
        cf = os.path.join(CA_DIR, "Teams", nm + ".png")
        if os.path.isfile(cf):
            try: return file_b64(cf)
            except Exception: pass
    t = idx.get(team + "_Logo") or idx.get(team) or (idx.get(full + "_Logo") or idx.get(full) if full else None)
    if t:
        soff, ssize, w, h, fmt = t
        if fmt == 4 and w * h * 4 == ssize:
            try: return rgba_to_png_b64(read_resS(resS_path, soff, ssize), w, h)
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

def logo_small(team, full, idx, resS_path, maxpx=120):
    # downscaled crest for the ranking list / team cards (keeps 500 logos light)
    for nm in (team, full):
        if not nm: continue
        cf = os.path.join(CA_DIR, "Teams", nm + ".png")
        if os.path.isfile(cf):
            try: return tournament_logo_b64(cf, maxpx)
            except Exception: pass
    t = idx.get(team + "_Logo") or idx.get(team) or (idx.get(full + "_Logo") or idx.get(full) if full else None)
    if t:
        soff, ssize, w, h, fmt = t
        if fmt == 4 and w * h * 4 == ssize:
            try: return _scale_png_b64(read_resS(resS_path, soff, ssize), w, h, maxpx)
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
            H = rows[0]
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

def parse_tournaments_raw(root):
    """root[1] holds every finished tournament: entry[0]=[.,.,guid,NAME,...],
    entry[1]=[7,[[[team,place,prize,pts],...]]]. Return [{name,guid,standings{team:place}}]."""
    out = []
    t1 = A(at(root, 1)) or []
    for e in t1:
        a = A(e)
        if not a or len(a) < 2:
            continue
        info = A(a[0])
        if not info or len(info) < 4:
            continue
        name = S(at(info, 3)); guid = S(at(info, 2))
        if not name:
            continue
        standings = {}
        table = []   # detailed: [{team, place, prize, pts}]
        res = A(a[1])
        if res and len(res) > 1:
            outer = A(res[1])
            rows = A(outer[0]) if (outer and len(outer) > 0) else None
            if rows:
                for row in rows:
                    r = A(row)
                    if r and len(r) >= 2 and S(at(r, 0)):
                        tm = S(at(r, 0)); pl = L(at(r, 1))
                        standings[tm] = pl
                        table.append({"team": tm, "place": pl,
                                      "prize": L(at(r, 2)) if len(r) > 2 else 0,
                                      "pts": L(at(r, 3)) if len(r) > 3 else 0})
        table.sort(key=lambda x: x["place"])
        out.append({"name": name, "guid": guid or "", "standings": standings, "table": table})
    return out

def parse_transfers(root):
    """root[5] = world transfers: [.,.,age,NICK,None,TO_team,FROM_team,FEE,...]."""
    out = []
    for e in (A(at(root, 5)) or []):
        a = A(e)
        if not a or len(a) < 8:
            continue
        nick = S(at(a, 3))
        if not nick:
            continue
        out.append({"nick": nick, "to": S(at(a, 5)) or "", "from": S(at(a, 6)) or "",
                    "fee": L(at(a, 7)), "age": L(at(a, 2))})
    return out

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
            if str(tier).strip().upper() == "MAJOR" or "major" in nm.lower():
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
    guid2t = {t["guid"]: t for t in tourns if t.get("guid")}
    team_won = {}
    for t in tourns:
        mj = t["name"] in majors
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
                won.append((t["name"], t["name"] in majors))
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
        tourn_list.append({"name": t["name"], "major": 1 if t["name"] in majors else 0,
                           "table": t["table"], "bracket": build_bracket(t["table"])})
    icons = {}
    for nm in names:
        ic = tournament_icon(nm)
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
    """Key a tournament by its (unique, numbered) name — each event instance appears
    once, so it can never be counted twice no matter how many times it's captured."""
    sig = (t.get("name", "") or "")
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
    td = arch["tournaments"]
    seq = arch.get("seq", 0)
    # one-time cleanup: collapse any duplicate records of the same (name, winner)
    # left over from the old content-hash keying, so nothing is counted twice.
    seen = {}
    for k in list(td.keys()):
        r = td[k]
        dk = r.get("name")
        if dk in seen:
            # keep the record that actually has a champion (more complete)
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
        key = _tourn_key(t, winner)
        if key in td:
            continue
        seq += 1
        td[key] = {"name": t["name"], "guid": t.get("guid", ""), "tier": t.get("tier", 0),
                   "major": 1 if t["name"] in majors else 0,
                   "standings": t["standings"], "table": t["table"],
                   "winner": winner, "roster": roster, "mvp": mvp, "evp": evp, "seq": seq}
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
    D["tournaments"] = [{"name": r["name"], "guid": r.get("guid", ""), "tier": r.get("tier", 0),
                         "standings": r["standings"], "table": r["table"]} for r in recs]
    awards, won, team_won = {}, {}, {}
    # MVP awards come straight from the game's tournament database (root[51]) — the
    # REAL MVP of every finished event, exactly as the game shows it.
    for tname, mvpnick in (D.get("tourn_mvp") or {}).items():
        if not mvpnick:
            continue
        a = awards.setdefault(mvpnick, {"mvp": 0, "evp": 0, "mvpEvents": [], "evpEvents": []})
        a["mvp"] += 1; a["mvpEvents"].append(tname)
    # trophies (players' titles) + team titles from the accumulated standings history
    for r in recs:
        if r.get("winner"):
            team_won.setdefault(r["winner"], []).append((r["name"], r.get("major", 0)))
        for nk in (r.get("roster") or []):
            won.setdefault(nk, []).append((r["name"], r.get("major", 0)))
    D["archived_awards"] = awards
    D["archived_won"] = won
    D["archived_team_won"] = team_won

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
        cur_pts = int(round(pv[-1])) if pv else 0
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
    for e in (A(at(root, 51)) or []):
        a = A(e)
        if not a or len(a) < 11 or not isinstance(a[0], str):
            continue
        name = a[0]; tier = L(at(a, 4)); prize = L(at(a, 6))
        if tier == 1 and prize < 300000:      # drop the many tiny regional/filler events
            continue
        out.append({"name": name, "tier": tier, "tierName": TIER_NAME.get(tier, "Tier 2"),
                    "rating": round(Dd(at(a, 5)), 2), "prize": prize,
                    "city": S(at(a, 10)) or "", "country": S(at(a, 9)) or ""})
    return out

def build_payload(D, photos, team_logo, tlogos):
    my = D["myTeam"]
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
    calendar = []
    for c in D.get("catalog", []):
        cc = dict(c); cc["done"] = 1 if c["name"] in done_names else 0
        cc["winner"] = winners.get(c["name"], "")
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
        if p.get("stats"):
            o["stats"] = {k2: v2 for k2, v2 in p["stats"].items() if k2 != "raw"}   # drop bulky raw
        if p.get("career"): o["career"] = p["career"]                                # all-time totals
        if p.get("retired"): o["retired"] = 1
        vhh = (D.get("vhist") or {}).get(nk)
        if vhh and len(vhh) > 1: o["vhist"] = vhh                                     # value/rating over time
        te = team_earnings.get(p["team"], 0)
        if te and team_count.get(p["team"]):
            o["earnings"] = int(te / team_count[p["team"]])   # even split of team prize
        if p.get("medals"): o["medals"] = p["medals"]
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
    return {
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
        "transfers": D.get("transfers", [])[:250],
        "free_agents": free_agents,
        "talents": talents,
        "top_value": top_value,
        "bargains": bargains,
        "tournaments": tourn_list,
        "calendar": calendar,
        "photos": photos,
        "logos": _merge_logos(my, team_logo, tlogos),
        "flags": load_flags(),
    }

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
    for nk, b in custom_photos_all().items():   # user custom photos override everything
        photos[nk] = b
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
