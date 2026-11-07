"""hi: get everything in a Humble account into Steam (macOS and Linux).

    hi keys    [--reveal none|needed|all]   pull every key + order from Humble (humble.py extract)
    hi plan                                  what each owned item will go through, by route
    hi apply   [--only ROUTE ...] [--dry-run]
               steam keys     -> a local page of prefilled Steam activation links
               DRM-free apps  -> download, unpack, Steam shortcut (Windows builds via Wine/umu wrapper)
               ebooks/audio/files -> download into the library folder
               vendor routes  -> redemption steps (and the vendor's installers, where a recipe drives them)

Config (optional): ~/.config/humble-integrator/config.toml
    library = "~/Humble"            games = "~/Games/Humble"
    wine = "/path/to/wine"          # macOS: CrossOver/Kegworks wine for Windows-only builds
    umu = "umu-run"                 # Linux
    accept_eulas = ["profantasy"]   # vendors whose EULA you accept for unattended installs
    steam_api_key = "..."           # steamcommunity.com/dev/apikey: lets --reveal needed skip owned games
    steamgriddb_api_key = "..."     # steamgriddb.com/profile/preferences/api: shortcut artwork
"""

import argparse
import html
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tomllib
import webbrowser
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import humble  # noqa: E402
import install  # noqa: E402
import recipes  # noqa: E402
import steam  # noqa: E402

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "humble-integrator" / "config.toml"
GAME_PLATFORMS = {"windows", "mac", "linux"}


def tilde(path):
    """Show paths under the home directory as ~/... (also keeps usernames out of pasted output)."""
    path, home = str(path), str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def load_config():
    cfg = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cfg.setdefault("library", "~/Humble")
    cfg.setdefault("games", "~/Games/Humble")
    cfg.setdefault("umu", "umu-run")
    for k in ("library", "games"):
        cfg[k] = Path(cfg[k]).expanduser()
    return cfg


# ---------------------------------------------------------------- account -> items

def account_items(orders):
    """Census-shaped items for every downloadable subproduct the account owns."""
    for order in orders:
        product = order.get("product") or {}
        bundle = {"slug": (product.get("machine_name") or "").replace("_", "-"),
                  "human_name": product.get("human_name", "")}
        for sub in order.get("subproducts") or []:
            dls = [d for d in humble.downloads_in_order({"gamekey": order.get("gamekey"), "subproducts": [sub]})]
            if not dls:
                continue  # key-only items are planned from their keys
            plats = {d.platform for d in dls}
            if plats & GAME_PLATFORMS:
                content, delivery = "game", ["hb-drmfree"]
            elif "ebook" in plats:
                content, delivery = "ebook", ["hb-drmfree"]
            elif "audio" in plats:
                content, delivery = "music", ["hb-audio"]
            else:
                content, delivery = None, ["hb-drmfree"]
            payee = (sub.get("payee") or {}).get("human_name")
            yield {"machine_name": sub.get("machine_name", ""), "human_name": sub.get("human_name", ""),
                   "content_type": content, "delivery": delivery, "developers": [payee] if payee else [],
                   "publishers": [], "downloads": dls}, bundle


def make_plan(book, orders, keys, system=None):
    plan = defaultdict(list)  # route id -> entries
    for item, bundle in account_items(orders):
        recipe, layer = book.resolve(item, bundle)
        rid = recipe.id if recipe else "(uncovered)"
        plan[rid].append({"kind": "download", "item": item, "bundle": bundle,
                          "build": humble.pick_build(item["downloads"], system) if item["content_type"] == "game"
                          else item["downloads"]})
    for key in keys:
        if key.is_expired:
            continue
        recipe, layer = book.resolve_key(key)
        plan[recipe.id if recipe else "(uncovered)"].append({"kind": "key", "key": key})
    return plan


def print_plan(book, plan, out=sys.stdout):
    for rid in sorted(plan, key=lambda r: (r == "(uncovered)", r)):
        entries = plan[rid]
        name = book.recipes[rid].data.get("name") if rid in book.recipes else "no recipe yet"
        print(f"{rid}  ({name}): {len(entries)}", file=out)
        for e in entries[:8]:
            if e["kind"] == "key":
                k = e["key"]
                print(f"    key  {k.human_name}  [{k.key_type}, {'revealed' if k.revealed else 'unrevealed'}]", file=out)
            else:
                files = ", ".join(f"{d.platform}:{d.filename}" for d in e["build"][:3]) or "no build for this OS"
                print(f"    file {e['item']['human_name']}  ({files})", file=out)
        if len(entries) > 8:
            print(f"    ... {len(entries) - 8} more", file=out)


# ---------------------------------------------------------------- apply

STEAM_PAGE_CSS = """
:root { color-scheme: light dark; --bg: #f6f7f9; --card: #fff; --fg: #1b1f24; --muted: #5b6470;
  --accent: #1a73e8; --line: #dde1e6; }
@media (prefers-color-scheme: dark) { :root { --bg: #15181c; --card: #1e2227; --fg: #e8eaed;
  --muted: #9aa3ad; --accent: #7cacf8; --line: #2e333a; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 640px; margin: 40px auto; padding: 0 16px; }
h1 { font-size: 22px; margin: 0 0 4px; } p { color: var(--muted); margin: 0 0 20px; }
ol { list-style: none; padding: 0; margin: 0; }
li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px;
  background: var(--card); border: 1px solid var(--line); border-radius: 8px; margin-bottom: 8px; }
a.go { color: var(--accent); text-decoration: none; font-weight: 600; white-space: nowrap; }
.muted { color: var(--muted); font-size: 13px; } code { font-size: 13px; }
"""


def steam_key_page(entries, path):
    rows = []
    for e in entries:
        k = e["key"]
        name = html.escape(k.human_name)
        if not k.revealed:
            rows.append(f'<li><span>{name}</span><span class="muted">not revealed yet: '
                        f'<code>hi keys --reveal needed</code></span></li>')
            continue
        url = "https://store.steampowered.com/account/registerkey?key=" + html.escape(k.value)
        rows.append(f'<li><span>{name}</span><a class="go" href="{url}" target="_blank">Activate on Steam</a></li>')
    humble.write_private(Path(path), "<!doctype html><meta charset=utf-8>"
                         "<meta name=viewport content='width=device-width'><title>Steam keys</title>"
                         f"<style>{STEAM_PAGE_CSS}</style><main><h1>Activate on Steam</h1>"
                         "<p>Each link opens Steam's activation page with the key already filled in.</p>"
                         "<ol>" + "\n".join(rows) + "</ol></main>")
    return path


def windows_wrapper(cfg, prefix, exe_windows_path, script_path):
    """A launch script that runs a Windows program in a fixed prefix (shortcut target)."""
    if platform.system() == "Linux":
        cmd = f'GAMEID=0 WINEPREFIX={shlex.quote(str(prefix))} exec {shlex.quote(cfg["umu"])} {shlex.quote(exe_windows_path)} "$@"'
    else:
        if not cfg.get("wine"):
            raise SystemExit("Windows-only build on macOS: set wine = \"/path/to/wine\" in " + str(CONFIG))
        cmd = f'WINEPREFIX={shlex.quote(str(prefix))} exec {shlex.quote(cfg["wine"])} {shlex.quote(exe_windows_path)} "$@"'
    script_path.write_text("#!/bin/sh\n" + cmd + "\n")
    script_path.chmod(0o755)
    return script_path


def install_build(cfg, entry, log=print):
    """Download and install one DRM-free app; returns (shortcut name, exe) or None."""
    item, build = entry["item"], entry["build"]
    if not build:
        log(f"  skip {item['human_name']}: no build for this OS")
        return None
    d = build[0]
    home = cfg["games"] / item["machine_name"]
    archive = humble.fetch(d, home / "download")
    if d.platform == "windows":
        prefix = home / "prefix"
        prefix.mkdir(parents=True, exist_ok=True)
        app_dir = home / "app"
        kind = install.installer_kind(archive) if archive.suffix.lower() in (".exe", ".msi") else "archive"
        if kind == "archive":
            install.unpack(archive, app_dir)
            exe = install.find_launcher(app_dir, "Windows", item["human_name"])
            if not exe:
                return keep_as_files(cfg, item, app_dir, log)
            exe_win = f"Z:{exe}".replace("/", "\\")
        else:
            target = r"C:\Games\App"
            cmd = install.silent_command(archive, kind, target)
            runner = [cfg["umu"]] if platform.system() == "Linux" else [cfg.get("wine") or "wine"]
            env = dict(os.environ, WINEPREFIX=str(prefix), GAMEID="0")
            log(f"  installing {item['human_name']} ({kind}){'' if cmd else ' interactively'}")
            subprocess.run(runner + (cmd or [str(archive)]), env=env, check=False)
            exe = install.find_launcher(prefix / "drive_c" / "Games" / "App", "Windows", item["human_name"]) \
                or install.find_launcher(prefix / "drive_c", "Windows", item["human_name"])
            exe_win = ("C:\\" + str(exe.relative_to(prefix / "drive_c")).replace("/", "\\")) if exe else None
        if not exe_win:
            log(f"  installed {item['human_name']} but couldn't find its exe; add the shortcut by hand")
            return None
        return item["human_name"], windows_wrapper(cfg, prefix, exe_win, home / "launch.sh")
    app_dir = install.unpack(archive, home / "app")
    exe = install.find_launcher(app_dir, platform.system(), item["human_name"])
    if not exe:
        return keep_as_files(cfg, item, app_dir, log)
    return item["human_name"], exe


def keep_as_files(cfg, item, unpacked, log):
    """A 'DRM-free app' with nothing to launch is an asset pack (samples, textures):
    move it to the library instead of leaving it among the games."""
    dest = cfg["library"] / "files" / item["machine_name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(unpacked), dest)
    log(f"  {item['human_name']}: nothing to launch, so it's files -> {tilde(dest)}")
    return None


def apply(cfg, book, plan, only=None, dry_run=False, log=print):
    shortcuts = []
    redeem_groups = {}  # (where, steps) -> key names: one set of instructions per vendor
    for rid, entries in plan.items():
        if only and rid not in only:
            continue
        if rid == "generic/steam-key":
            page = cfg["library"] / "steam-keys.html"
            log(f"{len(entries)} Steam keys -> {tilde(page)}")
            if not dry_run:
                cfg["library"].mkdir(parents=True, exist_ok=True)
                webbrowser.open(steam_key_page(entries, page).as_uri())
        elif rid == "generic/drm-free-app":
            for e in entries:
                log(f"app {e['item']['human_name']}")
                if not dry_run:
                    got = install_build(cfg, e, log)
                    if got:
                        shortcuts.append(got)
        elif rid == "generic/humble-coupon":
            log(f"{len(entries)} Humble coupon(s): nothing to do; they apply at humblebundle.com checkout")
        elif rid in ("generic/ebook", "generic/audio", "generic/files"):
            for e in entries:
                dest = cfg["library"] / rid.split("/")[1] / e["item"]["machine_name"]
                for d in e["build"]:
                    log(f"file {e['item']['human_name']}: {d.filename}")
                    if not dry_run:
                        humble.fetch(d, dest)
        elif rid in book.recipes:
            r = book.recipes[rid]
            vendor = book.recipes.get(f"vendors/{r.vendor}") if r.vendor else None
            # An item recipe's own [redeem] (e.g. a Unity voucher inside a Fab bundle) wins.
            redeem = r.data.get("redeem") or (vendor.data.get("redeem", {}) if vendor else {})
            keyed = [e for e in entries if e["kind"] == "key"]
            files = [e for e in entries if e["kind"] == "download"]
            if keyed:
                group = redeem_groups.setdefault((redeem.get("where", "?"), tuple(redeem.get("steps", []))), [])
                group.extend(e["key"].human_name for e in keyed)
            # Items on a vendor route can also have real Humble downloads: install apps
            # (item recipes of kind "app"), fetch everything else into the library.
            for e in files:
                if r.layer == "item" and r.kind == "app":
                    log(f"app {e['item']['human_name']} ({rid})")
                    if not dry_run:
                        got = install_build(cfg, dict(e, build=humble.pick_build(e["item"]["downloads"])), log)
                        steam_cfg = r.data.get("steam", {})
                        if got and steam_cfg.get("shortcut", True):
                            shortcuts.append((steam_cfg.get("title") or got[0], got[1]))
                    continue
                dest = cfg["library"] / (r.vendor or rid.replace("/", "-")) / e["item"]["machine_name"]
                for d in e["build"] or e["item"]["downloads"]:
                    log(f"file {e['item']['human_name']}: {d.filename}")
                    if not dry_run:
                        humble.fetch(d, dest)
        else:
            log(f"{rid}: {len(entries)} item(s) have no recipe yet")
    for (where, steps), names in redeem_groups.items():
        log(f"redeem {len(names)} key(s) at {where}: {', '.join(names)}")
        for step in steps:
            log(f"    - {step}")
    if shortcuts and not dry_run:
        if steam.steam_running():
            log("Steam is running: quit it and re-run `hi apply --only generic/drm-free-app` to add shortcuts")
        else:
            made = [steam.make_shortcut(n, str(x)) for n, x in shortcuts]
            art = {}
            if cfg.get("steamgriddb_api_key"):
                for (sid, entry) in made:
                    try:
                        art[sid] = steam.steamgriddb_art(cfg["steamgriddb_api_key"], entry["AppName"])
                    except Exception as exc:  # artwork is nice-to-have
                        log(f"  no artwork for {entry['AppName']}: {exc}")
            for root in steam.steam_roots():
                for c in steam.user_config_dirs(root):
                    steam.upsert_shortcuts(c, [entry for _, entry in made])
                    for sid, images in art.items():
                        steam.write_artwork(c, sid, images)
            log(f"added {len(shortcuts)} Steam shortcuts ({len([a for a in art.values() if a])} with artwork)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="hi", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keys")
    k.add_argument("--reveal", choices=("none", "needed", "all"), default="none")
    k.add_argument("--cookie-file")
    sub.add_parser("plan")
    a = sub.add_parser("apply")
    a.add_argument("--only", nargs="*")
    a.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "keys":
        cfg, extra = load_config(), []
        if args.reveal == "needed":
            if cfg.get("steam_api_key"):
                owned = humble.data_dir() / "steam-owned.txt"
                steam.main(["owned", "--api-key", cfg["steam_api_key"], "--out", str(owned)])
                extra = ["--steam-owned", str(owned)]
            else:
                print("no steam_api_key in config: Steam keys stay unrevealed (ownership unknown); "
                      "other keys are revealed", file=sys.stderr)
        return humble.main(["extract", "--reveal", args.reveal] + extra
                           + (["--cookie-file", args.cookie_file] if args.cookie_file else []))
    cfg, book = load_config(), recipes.load()
    orders, keys = humble.cached_orders(), list(humble.KeyStore().keys.values())
    if not orders and not keys:
        raise SystemExit("nothing extracted yet: run `hi keys` first")
    plan = make_plan(book, orders, keys)
    if args.cmd == "plan":
        print_plan(book, plan)
    else:
        apply(cfg, book, plan, set(args.only or []), args.dry_run)


if __name__ == "__main__":
    main()
