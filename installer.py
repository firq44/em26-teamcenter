# -*- coding: utf-8 -*-
# EM2026 modpack installer.
#
# A friend runs this ONE .exe. It finds the game, downloads the WHOLE modpack
# from GitHub (all mods, the fixed VRS/ERS mod, the disabled match-override mod,
# the Team Center dashboard, configs and the default database), installs each
# file to the right place, and turns on live auto-update. After that the modpack
# keeps itself up to date on every game launch (F3 in-game).
#
# Requires BepInEx to already be installed in the game folder (the pack ships mod
# plugins and configs, not the BepInEx runtime itself).
import os, sys, io, json, hashlib, urllib.request, urllib.parse, re, time

# Cyrillic-safe console on any Windows codepage / when redirected to a file.
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)
except Exception:
    pass
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://raw.githubusercontent.com/firq44/em26-teamcenter/main/"
GAME = "Esports Manager 2026"
GAME_PROC = "EsportsManager.exe"


def low_dir():
    return os.path.join(os.path.expanduser("~"), "AppData", "LocalLow",
                        "NeuronaGames", "EsportsManager")


def find_game_dir():
    cands = []
    for drv in ["C", "D", "E", "F", "G", "H"]:
        cands.append(r"%s:\Program Files (x86)\Steam\steamapps\common\%s" % (drv, GAME))
        cands.append(r"%s:\SteamLibrary\steamapps\common\%s" % (drv, GAME))
        cands.append(r"%s:\Games\Steam\steamapps\common\%s" % (drv, GAME))
        cands.append(r"%s:\Steam\steamapps\common\%s" % (drv, GAME))
    for vdf in [r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf",
                r"C:\Program Files\Steam\steamapps\libraryfolders.vdf"]:
        try:
            if os.path.isfile(vdf):
                txt = io.open(vdf, encoding="utf-8", errors="ignore").read()
                for p in re.findall(r'"path"\s*"([^"]+)"', txt):
                    p = p.replace("\\\\", "\\")
                    cands.append(os.path.join(p, "steamapps", "common", GAME))
        except Exception:
            pass
    seen = set()
    for game in cands:
        if game in seen:
            continue
        seen.add(game)
        if os.path.isfile(os.path.join(game, "EsportsManager.exe")):
            return game
    return None


def download(url, timeout=180):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "EM26-Installer"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def game_running():
    try:
        import subprocess
        flags = 0x08000000 if os.name == "nt" else 0
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + GAME_PROC],
                             capture_output=True, text=True,
                             creationflags=flags, timeout=10).stdout or ""
        return GAME_PROC.lower() in out.lower()
    except Exception:
        return False


def target_for(game, relpath):
    parts = relpath.split("/")
    if parts[0] == "_lowdb":
        return os.path.join(low_dir(), *parts[1:]), True   # True = active database
    return os.path.join(game, *parts), False


def sha(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return ""


def main():
    print("=" * 56)
    print("  Esports Manager 2026 - установка модпака")
    print("=" * 56)
    print()

    game = find_game_dir()
    if not game:
        print("Не нашёл игру автоматически.")
        print("Впиши путь к папке игры (где лежит EsportsManager.exe) и Enter:")
        game = input("> ").strip().strip('"')
    if not game or not os.path.isfile(os.path.join(game, "EsportsManager.exe")):
        print("\nПапка игры не найдена по этому пути.")
        return pause()
    if not os.path.isdir(os.path.join(game, "BepInEx")):
        print("\nВ папке игры нет BepInEx.")
        print("Сначала установи BepInEx (IL2CPP), потом запусти этот установщик снова.")
        return pause()

    print("Игра найдена:")
    print("  " + game)
    if game_running():
        print()
        print("  [!] Игра сейчас запущена. Закрой её и запусти установщик заново,")
        print("      иначе часть модов (.dll) не получится обновить.")
        return pause()
    print()
    print("Скачиваю список файлов...")
    try:
        manifest = json.loads(download(BASE + "manifest.json", 30).decode("utf-8"))
    except Exception as e:
        print("  [x] не удалось скачать manifest.json: %s" % e)
        print("  Проверь интернет и попробуй ещё раз.")
        return pause()

    want = manifest.get("files") or {}
    tree = {k: v for k, v in want.items() if "/" in k}
    total = len(tree)
    print("Файлов в модпаке: %d. Устанавливаю...\n" % total)

    ok = skipped = failed = 0
    for i, relpath in enumerate(sorted(tree), 1):
        remote = tree[relpath]
        tgt, is_db = target_for(game, relpath)
        if sha(tgt) == remote:
            skipped += 1
            continue
        try:
            data = download(BASE + "files/" + urllib.parse.quote(relpath, safe="/"))
            if hashlib.sha256(data).hexdigest() != remote:
                print("  [!] %s - хеш не совпал, пропускаю" % relpath)
                failed += 1
                continue
            # active career database: keep a backup before replacing
            if is_db and os.path.isfile(tgt):
                bak = tgt + ".bak_before_modpack"
                try:
                    if not os.path.isfile(bak):
                        import shutil
                        shutil.copy2(tgt, bak)
                except Exception:
                    pass
            d = os.path.dirname(tgt)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = tgt + ".new"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, tgt)
            ok += 1
            if i % 8 == 0 or i == total:
                print("  ... %d/%d" % (i, total))
        except Exception as e:
            print("  [x] %s: %s" % (relpath, e))
            failed += 1

    # make sure live auto-update is on (base URL for the self-updater)
    try:
        tc = os.path.join(game, "BepInEx", "plugins", "TeamCenter")
        os.makedirs(tc, exist_ok=True)
        with io.open(os.path.join(tc, "update_config.json"), "w", encoding="utf-8") as f:
            json.dump({"base_url": BASE}, f, indent=2)
    except Exception as e:
        print("  [x] update_config.json: %s" % e)

    print()
    print("-" * 56)
    print("Готово. Установлено/обновлено: %d, уже актуально: %d, ошибок: %d."
          % (ok, skipped, failed))
    print("Запусти игру и в игре нажми F3 - откроется Team Center.")
    print("Дальше модпак обновляется сам при каждом запуске игры.")
    print("-" * 56)
    pause()


def pause():
    try:
        input("\nEnter для выхода...")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        pass
    except Exception as e:
        print("Ошибка: %s" % e)
        pause()
