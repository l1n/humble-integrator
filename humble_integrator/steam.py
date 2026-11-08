"""Non-Steam shortcuts: shortcuts.vdf, grid artwork, notes, and Proton mapping.

Every entry this tool writes carries the tag "Humble" and is keyed by its
Steam shortcut id, so re-running updates our entries and never touches the
user's own. Steam rewrites these files on exit, so nothing is written while
Steam is running.

    python3 -I humble_integrator/steam.py list
    python3 -I humble_integrator/steam.py add --name NAME --exe PATH [--start-dir DIR] [--proton NAME]
"""

import argparse
import binascii
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

TAG = "Humble"


# ---------------------------------------------------------------- locations

def steam_roots():
    home = Path.home()
    if platform.system() == "Darwin":
        candidates = [home / "Library/Application Support/Steam"]
    else:
        candidates = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
                      home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    seen, roots = set(), []
    for c in candidates:
        if (c / "userdata").is_dir():
            real = c.resolve()
            if real not in seen:
                seen.add(real)
                roots.append(c)
    return roots


def user_config_dirs(root):
    return sorted(d / "config" for d in (root / "userdata").iterdir()
                  if d.name.isdigit() and d.name != "0" and (d / "config").is_dir())


def target_config_dirs(root, user=None):
    """The Steam account to write to: USER (account name or userdata id) if given, else the one
    that logged in most recently, so other people's accounts on a shared machine are left alone."""
    dirs = user_config_dirs(root)
    try:
        users = vdf_loads((root / "config" / "loginusers.vdf").read_text()).get("users", {})
    except (OSError, ValueError):
        users = {}
    by_id = {str(int(k) - STEAMID64_BASE): {f.lower(): v for f, v in u.items()}
             for k, u in users.items() if k.isdigit() and isinstance(u, dict)}
    if user:
        return [d for d in dirs if str(user) in (d.parent.name, by_id.get(d.parent.name, {}).get("accountname"))]
    known = [d for d in dirs if d.parent.name in by_id]
    if not known:
        return dirs
    recent = lambda d: (by_id[d.parent.name].get("mostrecent") == "1", int(by_id[d.parent.name].get("timestamp") or 0))
    return [max(known, key=recent)]


def steam_running():
    names = ["steam_osx"] if platform.system() == "Darwin" else ["steam", "steamwebhelper"]
    for n in names:
        if subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0:
            return True
    return False


# ---------------------------------------------------------------- ids

def shortcut_id(exe, name):
    """Steam's id for a non-Steam shortcut: crc32(exe + name) with the top bit set."""
    return (binascii.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def game_id(sid):
    """64-bit id used in steam://rungameid/ URLs."""
    return (sid << 32) | 0x02000000


# ---------------------------------------------------------------- binary VDF

T_MAP, T_STR, T_INT, T_END = 0, 1, 2, 8


def _cstr(data, i):
    j = data.index(b"\x00", i)
    return data[i:j].decode("utf-8", "replace"), j + 1


def bvdf_load(data):
    def read_map(i):
        out = {}
        while True:
            t = data[i]
            i += 1
            if t == T_END:
                return out, i
            key, i = _cstr(data, i)
            if t == T_MAP:
                out[key], i = read_map(i)
            elif t == T_STR:
                out[key], i = _cstr(data, i)
            elif t == T_INT:
                out[key] = struct.unpack_from("<i", data, i)[0]
                i += 4
            else:
                raise ValueError(f"unsupported binary VDF type {t} at {i - 1}")
    result, _ = read_map(0)
    return result


def bvdf_dump(obj):
    out = bytearray()

    def write_map(m):
        for k, v in m.items():
            key = str(k).encode("utf-8") + b"\x00"
            if isinstance(v, dict):
                out.extend(bytes([T_MAP]) + key)
                write_map(v)
            elif isinstance(v, bool) or isinstance(v, int):
                out.extend(bytes([T_INT]) + key + struct.pack("<i", _to_i32(int(v))))
            else:
                out.extend(bytes([T_STR]) + key + str(v).encode("utf-8") + b"\x00")
        out.append(T_END)

    write_map(obj)
    return bytes(out)


def _to_i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


# ---------------------------------------------------------------- text VDF (config.vdf)

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)', re.S)


def vdf_loads(text):
    stack, cur, key = [], {}, None
    for m in _TOKEN.finditer(text):
        s, brace, comment = m.groups()
        if comment:
            continue
        if brace == "{":
            new = {}
            cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop()
        elif key is None:
            key = s.replace('\\"', '"').replace("\\\\", "\\")
        else:
            cur[key] = s.replace('\\"', '"').replace("\\\\", "\\")
            key = None
    return cur


def vdf_dumps(obj, indent=0):
    tab = "\t" * indent
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    lines = []
    for k, v in obj.items():
        if isinstance(v, dict):
            lines.append(f'{tab}"{esc(k)}"\n{tab}{{\n{vdf_dumps(v, indent + 1)}{tab}}}\n')
        else:
            lines.append(f'{tab}"{esc(k)}"\t\t"{esc(v)}"\n')
    return "".join(lines)


# ---------------------------------------------------------------- shortcuts

def _quote(path):
    return path if path.startswith('"') else f'"{path}"'


def make_shortcut(name, exe, start_dir=None, launch_options="", icon="", tags=()):
    exe_q = _quote(exe)
    sid = shortcut_id(exe_q, name)
    return sid, {
        "appid": _to_i32(sid),
        "AppName": name,
        "Exe": exe_q,
        "StartDir": _quote(start_dir or str(Path(exe.strip('"')).parent)),
        "icon": icon,
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {str(i): t for i, t in enumerate([TAG, *tags])},
    }


def read_shortcuts(config_dir):
    path = Path(config_dir) / "shortcuts.vdf"
    if not path.exists():
        return {}
    return bvdf_load(path.read_bytes()).get("shortcuts", {})


def backup(path):
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"))


def upsert_shortcuts(config_dir, entries, remove_ids=()):
    """Add or replace our shortcuts (by id, or by name: an item reinstalled from another build
    gets a new id); drop ours listed in remove_ids. Others are untouched."""
    path = Path(config_dir) / "shortcuts.vdf"
    current = list(read_shortcuts(config_dir).values())
    by_id = {e["appid"]: e for e in entries}
    names = {e["AppName"] for e in entries}
    drop = {_to_i32(i) for i in remove_ids}
    merged = []
    for s in current:
        sid = s.get("appid")
        ours = TAG in (s.get("tags") or {}).values()
        if sid in by_id:
            merged.append(by_id.pop(sid))
        elif not (ours and (sid in drop or s.get("AppName") in names)):
            merged.append(s)
    merged.extend(by_id.values())
    backup(path)
    path.write_bytes(bvdf_dump({"shortcuts": {str(i): s for i, s in enumerate(merged)}}))
    return len(merged)


NOTES_APP = "2371090"  # Steam keeps game notes in this app's cloud folder
NOTE_ID = "humbleintegrator"


def notes_path(config_dir, shortcut_name):
    """A non-Steam game's notes file: named after its display name, ASCII punctuation and
    spaces as "_" (FilenameForNotes in Steam's client UI)."""
    safe = re.sub(r"[!-/:-@ \[\\\]^`]", "_", shortcut_name.strip())
    return Path(config_dir).parent / NOTES_APP / "remote" / f"notes_shortcut_{safe}"


def has_note(config_dir, shortcut_name):
    path = notes_path(config_dir, shortcut_name)
    try:
        return any(n.get("id") == NOTE_ID for n in json.loads(path.read_text()).get("notes", []))
    except (OSError, ValueError):
        return False


def add_note(config_dir, shortcut_name, title, bbcode):
    """Add our note (Steam's Notes panel, BBCode) to a shortcut's notes; Steam syncs them via
    Steam Cloud. The user's notes are kept, and so is ours once there: they may have edited it."""
    path, name = notes_path(config_dir, shortcut_name), shortcut_name.strip()
    data = {"notes": [], "shortcut_name": name}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            return False  # not ours to repair
        if any(n.get("id") == NOTE_ID for n in data.get("notes", [])):
            return False
        # back up outside remote/, which is Steam Cloud's
        shutil.copy2(path, path.parent.parent / f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    now = int(time.time())
    notes = data.setdefault("notes", [])
    notes.append({"id": NOTE_ID, "shortcut_name": name, "ordinal": len(notes), "time_created": now,
                  "time_modified": now, "title": title, "content": bbcode})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    track_cloud_file(path)
    return True


def track_cloud_file(path):
    """List a file we wrote in an app's remote/ folder in Steam Cloud's remotecache.vdf as a
    local change waiting to upload (syncstate 3). Steam only reads cloud files listed there,
    and uploads this one on its next sync. Steam rewrites the cache, so it must not be running."""
    app_dir = Path(path).parent.parent
    cache = app_dir / "remotecache.vdf"
    files = vdf_loads(cache.read_text()).get(app_dir.name, {}) if cache.exists() else {}
    if not files:
        files = {"ChangeNumber": "0", "ostype": _cloud_ostype(app_dir.parent)}
    data, mtime = Path(path).read_bytes(), str(int(Path(path).stat().st_mtime))
    files[Path(path).name] = {
        "root": "0", "size": str(len(data)), "localtime": mtime, "time": mtime,
        "remotetime": (files.get(Path(path).name) or {}).get("remotetime", "0"),
        "sha": hashlib.sha1(data).hexdigest(), "syncstate": "3", "persiststate": "0", "platformstosync2": "-1"}
    if cache.exists():
        shutil.copy2(cache, app_dir / f"remotecache.vdf.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    cache.write_text(vdf_dumps({app_dir.name: files}))


SCREENSHOTS_APP = "760"
NOT_UPLOADED = "18446744073709551615"


def has_screenshots(config_dir, sid):
    index = Path(config_dir).parent / SCREENSHOTS_APP / "screenshots.vdf"
    return index.exists() and bool(vdf_loads(index.read_text()).get("Screenshots", {}).get(str(game_id(sid))))


def add_screenshots(config_dir, sid, name, shots):
    """Fill a shortcut's Screenshots panel: shots = [(jpeg, thumbnail jpeg or None, caption)].
    Only for a shortcut with no screenshots yet, so the user's own are never mixed in. Local
    only: nothing is uploaded. Steam rewrites screenshots.vdf, so it must not be running."""
    gid = str(game_id(sid))
    base = Path(config_dir).parent / SCREENSHOTS_APP
    index = base / "screenshots.vdf"
    doc = vdf_loads(index.read_text()) if index.exists() else {}
    listed = doc.setdefault("Screenshots", {})
    if listed.get(gid) or not shots:
        return 0
    folder = base / "remote" / gid / "screenshots"
    (folder / "thumbnails").mkdir(parents=True, exist_ok=True)
    stamp, now, entries = time.strftime("%Y%m%d%H%M%S"), str(int(time.time())), {}
    for i, (jpeg, thumb, caption) in enumerate(shots):
        fn = f"{stamp}_{i + 1}.jpg"  # Steam's own naming: capture time, then a counter
        (folder / fn).write_bytes(jpeg)
        (folder / "thumbnails" / fn).write_bytes(thumb or jpeg)
        w, h = image_size(jpeg) or (0, 0)
        entries[str(i)] = {"type": "1", "filename": f"{gid}/screenshots/{fn}",
                           "thumbnail": f"{gid}/screenshots/thumbnails/{fn}", "imported": "1",
                           "width": str(w), "height": str(h), "gameid": gid, "creation": now,
                           "caption": caption, "Permissions": "2", "hscreenshot": NOT_UPLOADED}
    names = listed.pop("shortcutnames", {})
    listed[gid] = entries
    listed["shortcutnames"] = {**names, gid: name}  # Steam keeps this section last
    backup(index)
    index.write_text(vdf_dumps(doc))
    return len(entries)


def _cloud_ostype(user_dir):
    """The OS id Steam stamps on this machine's cloud caches (copied from another app's)."""
    for cache in sorted(Path(user_dir).glob("*/remotecache.vdf")):
        for files in vdf_loads(cache.read_text()).values():
            if isinstance(files, dict) and "ostype" in files:
                return files["ostype"]
    return "0"


ART = {"cover": "{id}p", "wide": "{id}", "hero": "{id}_hero", "logo": "{id}_logo", "icon": "{id}_icon"}


def write_artwork(config_dir, sid, images):
    """images: {"cover"|"wide"|"hero"|"logo"|"icon": (bytes, ".png"/".jpg")}."""
    grid = Path(config_dir) / "grid"
    grid.mkdir(exist_ok=True)
    written = []
    for kind, (data, ext) in images.items():
        p = grid / (ART[kind].format(id=sid) + ext)
        p.write_bytes(data)
        written.append(p)
    return written


def set_compat_tool(steam_root, sid, tool="proton_experimental"):
    """Force a compatibility tool (Proton) for one shortcut: config/config.vdf CompatToolMapping."""
    path = Path(steam_root) / "config" / "config.vdf"
    data = vdf_loads(path.read_text()) if path.exists() else {}
    node = data
    for k in ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"):
        node = node.setdefault(k, {})
    node[str(sid)] = {"name": tool, "config": "", "priority": "250"}
    backup(path)
    path.write_text(vdf_dumps(data))


# ---------------------------------------------------------------- artwork (SteamGridDB)

SGDB = "https://www.steamgriddb.com/api/v2"
UA = "humble-integrator/0.1"  # Cloudflare refuses Python-urllib's default agent (error 1010)
SGDB_QUERIES = {  # our artwork kind -> SteamGridDB endpoint + filters
    "cover": "grids/game/{id}?dimensions=600x900",
    "wide": "grids/game/{id}?dimensions=460x215,920x430",
    "hero": "heroes/game/{id}",
    "logo": "logos/game/{id}",
    "icon": "icons/game/{id}",
}


def similarity(a, b):
    from difflib import SequenceMatcher
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def image_size(data):
    """(width, height) of a PNG or JPEG image, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def art_from_images(urls, opener=None, limit=6):
    """Fallback artwork from a store's own item images: the first portrait one (a book or
    comic cover) as the cover, the first squarish one as the icon. Other shapes would stretch,
    and thumbnails (Humble's order API only has 70px ones) are only good enough for an icon."""
    import urllib.request
    opener = opener or urllib.request.build_opener()
    art = {}
    for url in [u for u in urls if u][:limit]:
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as resp:
                data = resp.read()
        except Exception:
            continue
        size = image_size(data)
        if not size or not size[0]:
            continue
        image, ratio = (data, ".png" if data[:4] == b"\x89PNG" else ".jpg"), size[1] / size[0]
        if ratio >= 1.2 and size[1] >= 300:
            art.setdefault("cover", image)
        elif 0.8 <= ratio <= 1.25:
            art.setdefault("icon", image)
        if {"cover", "icon"} <= art.keys():
            break
    return art


def steamgriddb_art(api_key, term, opener=None, min_similarity=0.8):
    """Fetch artwork for TERM: {"cover"|"wide"|"hero"|"logo"|"icon": (bytes, ext)} (missing kinds omitted)."""
    import json
    import urllib.parse
    import urllib.request
    opener = opener or urllib.request.build_opener()

    def get(path):
        req = urllib.request.Request(f"{SGDB}/{path}", headers={"Authorization": f"Bearer {api_key}", "User-Agent": UA})
        with opener.open(req, timeout=60) as resp:
            return resp.read()

    found = json.loads(get("search/autocomplete/" + urllib.parse.quote(term))).get("data") or []
    # Autocomplete always returns *something*; only take it when it's really the same title.
    if not found or similarity(found[0].get("name", ""), term) < min_similarity:
        return {}
    game_id = found[0]["id"]
    art = {}
    for kind, query in SGDB_QUERIES.items():
        try:
            hits = json.loads(get(query.format(id=game_id))).get("data") or []
        except Exception:
            continue
        if hits:
            url = hits[0]["url"]
            ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1] or ".png"
            with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as resp:
                art[kind] = (resp.read(), ext)
    return art


# ---------------------------------------------------------------- store page

STORE = "https://store.steampowered.com/api"


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def http_get(url, opener=None):
    import urllib.request
    opener = opener or urllib.request.build_opener()
    with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as resp:
        return resp.read()


def store_details(appid, opener=None):
    """The Steam store's public data for an app (name, developers, release date, genres,
    screenshots), or None."""
    got = json.loads(http_get(f"{STORE}/appdetails?appids={int(appid)}&cc=us&l=en", opener)).get(str(appid)) or {}
    return got.get("data") if got.get("success") else None


def store_match(name, developers=(), opener=None):
    """Store details for the game called NAME, or None: the same name ignoring case and
    punctuation ("Offspring Fling!"), else the longest store name ours starts with ("The
    Binding of Isaac" for "... + Wrath of the Lamb DLC") if its developer or publisher matches."""
    import urllib.parse
    want = _norm(name)
    # The search finds nothing for "... Isaac + Wrath of the Lamb DLC": then try "... Isaac".
    for term in dict.fromkeys([name, re.split(r"\s+[-+&:(]|:\s", name)[0]]):
        found = json.loads(http_get(f"{STORE}/storesearch/?term={urllib.parse.quote(term)}&cc=us&l=en", opener))
        items = found.get("items") or []
        if items:
            break
    exact = [i for i in items if _norm(i["name"]) == want]
    prefix = sorted((i for i in items if len(_norm(i["name"])) >= 6 and want.startswith(_norm(i["name"]))),
                    key=lambda i: -len(_norm(i["name"])))
    ours = {_norm(d) for d in developers if d}
    for item in (exact or prefix)[:3]:
        details = store_details(item["id"], opener)
        if not details:
            continue
        theirs = {_norm(d) for d in (details.get("developers") or []) + (details.get("publishers") or [])}
        same_maker = any(a in b or b in a for a in ours for b in theirs if a and b)
        if same_maker or len(exact) == 1:  # a lone exact name is enough; a prefix needs its maker
            return details
    return None


def store_screenshots(details, opener=None, limit=6):
    """[(jpeg, thumbnail jpeg)] from a store page: 1920x1080 shots with the store's 600px thumbnails."""
    shots = []
    for s in (details.get("screenshots") or [])[:limit]:
        try:
            shots.append((http_get(s["path_full"], opener), http_get(s["path_thumbnail"], opener)))
        except Exception:
            continue
    return shots


# ---------------------------------------------------------------- ownership

STEAMID64_BASE = 76561197960265728


def local_steam_ids():
    """SteamID64s of the accounts that have logged in to this machine's Steam."""
    ids = []
    for root in steam_roots():
        for cfg in user_config_dirs(root):
            ids.append(STEAMID64_BASE + int(cfg.parent.name))
    return sorted(set(ids))


def owned_app_ids(api_key, steamid, opener=None):
    """App ids owned by STEAMID, via the Steam Web API (key: steamcommunity.com/dev/apikey)."""
    import json
    import urllib.parse
    import urllib.request
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?" + urllib.parse.urlencode(
        {"key": api_key, "steamid": steamid, "include_played_free_games": 1, "skip_unvetted_apps": 0})
    with (opener or urllib.request.build_opener()).open(urllib.request.Request(url, headers={"User-Agent": UA}),
                                                         timeout=60) as resp:
        games = json.loads(resp.read()).get("response", {}).get("games", [])
    return {g["appid"] for g in games}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    o = sub.add_parser("owned", help="write owned app ids (for `hi keys --reveal needed`)")
    o.add_argument("--api-key", default=os.environ.get("STEAM_API_KEY"))
    o.add_argument("--steamid", type=int, action="append")
    o.add_argument("--out", required=True)
    a = sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--exe", required=True)
    a.add_argument("--start-dir")
    a.add_argument("--options", default="")
    a.add_argument("--proton", help="Linux: force this compatibility tool (e.g. proton_experimental)")
    a.add_argument("--user", help="Steam account name or userdata id (default: the most recent login)")
    args = p.parse_args(argv)
    if args.cmd == "owned":
        if not args.api_key:
            sys.exit("needs a Steam Web API key: --api-key or $STEAM_API_KEY (steamcommunity.com/dev/apikey)")
        owned = set()
        for sid in args.steamid or local_steam_ids():
            owned |= owned_app_ids(args.api_key, sid)
        Path(args.out).write_text("\n".join(str(a) for a in sorted(owned)) + "\n")
        print(f"{len(owned)} owned apps -> {args.out}")
        return
    roots = steam_roots()
    if not roots:
        sys.exit("no Steam installation with userdata found")
    if args.cmd == "list":
        for root in roots:
            for cfg in user_config_dirs(root):
                for s in read_shortcuts(cfg).values():
                    ours = "*" if TAG in (s.get("tags") or {}).values() else " "
                    print(f"{ours} {s.get('appid', 0) & 0xFFFFFFFF:10d}  {s.get('AppName')}  ->  {s.get('Exe')}  [{cfg.parent.name}]")
        return
    if steam_running():
        sys.exit("Steam is running; quit it first (it overwrites shortcuts.vdf on exit)")
    sid, entry = make_shortcut(args.name, args.exe, args.start_dir, args.options)
    for root in roots:
        for cfg in target_config_dirs(root, args.user):
            upsert_shortcuts(cfg, [entry])
            print(f"added {args.name} ({sid}) for Steam user {cfg.parent.name}")
        if args.proton:
            set_compat_tool(root, sid, args.proton)


if __name__ == "__main__":
    main()
