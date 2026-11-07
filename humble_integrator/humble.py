"""Automatic key extraction from a Humble account.

Reads every order in the account, collects every key on it (Steam keys and
every other kind: GOG, vendor serials, single "redeem at <vendor>" keys), and
saves them to a private keystore. Keys already revealed on Humble come back
as-is; unrevealed ones are revealed only with --reveal, because revealing is
irreversible (Humble stops offering a gift link for that key).

    python3 -I humble_integrator/humble.py extract [--reveal {none,needed,all}] [--type steam ...]
    python3 -I humble_integrator/humble.py keys    [--type ...] [--show]

Auth is the `_simpleauth_sess` cookie from a logged-in browser session, taken
from (first found): $HUMBLE_SESSION, --cookie-file, the macOS Keychain
(service "humble-integrator", account "_simpleauth_sess"), or the Linux secret
store (`secret-tool lookup service humble-integrator key _simpleauth_sess`).
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path

BASE = "https://www.humblebundle.com"
ORDERS_API = BASE + "/api/v1/user/order"
ORDER_API = BASE + "/api/v1/order/{gamekey}?all_tpkds=true"
REVEAL_API = BASE + "/humbler/redeemkey"
UA = "humble-integrator/0.1"
SECRET_SERVICE = "humble-integrator"
SESSION_COOKIE = "_simpleauth_sess"


def data_dir():
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "humble-integrator"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def write_private(path, text):
    """Write a file only the current user can read (it holds keys)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- auth

def session_cookie(cookie_file=None):
    if os.environ.get("HUMBLE_SESSION"):
        return os.environ["HUMBLE_SESSION"].strip()
    if cookie_file:
        text = Path(cookie_file).read_text().strip()
        if "\t" in text:  # Netscape/Mozilla cookies.txt export
            jar = MozillaCookieJar()
            jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
            for c in jar:
                if c.name == SESSION_COOKIE and "humblebundle.com" in c.domain:
                    return c.value
            raise SystemExit(f"no {SESSION_COOKIE} cookie for humblebundle.com in {cookie_file}")
        return text.removeprefix(SESSION_COOKIE + "=")
    if platform.system() == "Darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", SECRET_SERVICE,
                            "-a", SESSION_COOKIE, "-w"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if shutil.which("secret-tool"):
        r = subprocess.run(["secret-tool", "lookup", "service", SECRET_SERVICE, "key", SESSION_COOKIE],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise SystemExit(
        "No Humble session. Log in at humblebundle.com, copy the _simpleauth_sess cookie, then store it:\n"
        f"  macOS: security add-generic-password -s {SECRET_SERVICE} -a {SESSION_COOKIE} -w\n"
        f"  Linux: secret-tool store --label='Humble session' service {SECRET_SERVICE} key {SESSION_COOKIE}\n"
        "(both prompt for the value, so it stays out of shell history)")


class Humble:
    """Minimal client for Humble's (undocumented) library API."""

    def __init__(self, session, opener=None, delay=0.5):
        self.jar = CookieJar()
        self.session = session
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.delay = delay

    def _cookie_header(self):
        cookies = {SESSION_COOKIE: self.session}
        cookies.update({c.name: c.value for c in self.jar if "humblebundle.com" in c.domain})
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def _csrf(self):
        for c in self.jar:
            if c.name == "csrf_cookie":
                return c.value
        # Humble sets csrf_cookie on any page load.
        self._request(BASE + "/home/library", parse=False)
        return next((c.value for c in self.jar if c.name == "csrf_cookie"), "")

    def _request(self, url, data=None, headers=None, parse=True):
        h = {"User-Agent": UA, "Cookie": self._cookie_header(), "Accept": "application/json"}
        h.update(headers or {})
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
        for attempt in range(5):
            try:
                with self.opener.open(req, timeout=60) as resp:
                    raw = resp.read()
                    if getattr(resp, "geturl", lambda: url)().startswith(BASE + "/login"):
                        raise SystemExit("Humble session expired or invalid; log in again and re-store the cookie")
                    time.sleep(self.delay)
                    return json.loads(raw) if parse else raw
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise SystemExit(f"Humble refused the session ({e.code}); re-store a fresh cookie") from None
                if e.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                time.sleep(5 * 2 ** attempt)

    def gamekeys(self):
        return [o["gamekey"] for o in self._request(ORDERS_API)]

    def order(self, gamekey):
        return self._request(ORDER_API.format(gamekey=urllib.parse.quote(gamekey)))

    def reveal(self, key):
        """Reveal one key on Humble. Irreversible: the key can no longer be gifted."""
        resp = self._request(REVEAL_API, data={
            "keytype": key.machine_name, "key": key.gamekey, "keyindex": key.keyindex,
        }, headers={"csrf-prevention-token": self._csrf()})
        if not resp.get("success") or "error_msg" in resp:
            raise RuntimeError(f"Humble would not reveal {key.human_name}: {resp.get('error_msg', resp)}")
        return resp["key"]


# ---------------------------------------------------------------- keys

@dataclass
class Key:
    gamekey: str          # the order
    keyindex: int
    machine_name: str     # Humble's id for this key slot
    human_name: str
    key_type: str         # "steam", "gog", "generic", ...
    key_type_human_name: str
    bundle: str           # order's product name
    bundle_machine_name: str
    steam_app_id: int | None = None
    value: str | None = None      # the key itself, once revealed
    is_gift: bool = False
    is_expired: bool = False
    instructions: str = ""        # vendor redemption instructions (HTML), if any

    @property
    def id(self):
        return f"{self.gamekey}:{self.machine_name}:{self.keyindex}"

    @property
    def revealed(self):
        return bool(self.value)


def keys_in_order(order):
    product = order.get("product") or {}
    for tpk in (order.get("tpkd_dict") or {}).get("all_tpks") or []:
        yield Key(
            gamekey=tpk.get("gamekey") or order.get("gamekey"),
            keyindex=int(tpk.get("keyindex") or 0),
            machine_name=tpk.get("machine_name", ""),
            human_name=tpk.get("human_name", ""),
            key_type=tpk.get("key_type") or ("steam" if tpk.get("steam_app_id") else "unknown"),
            key_type_human_name=tpk.get("key_type_human_name") or "",
            bundle=product.get("human_name", ""),
            bundle_machine_name=product.get("machine_name", ""),
            steam_app_id=tpk.get("steam_app_id"),
            value=tpk.get("redeemed_key_val") or None,
            is_gift=bool(tpk.get("is_gift")),
            is_expired=bool(tpk.get("is_expired")),
            instructions=tpk.get("custom_instructions_html") or tpk.get("instructions_html") or "",
        )


@dataclass
class Download:
    machine_name: str     # the subproduct (matches census item machine names)
    human_name: str
    platform: str         # "mac", "linux", "windows", "ebook", "audio", "android", ...
    name: str             # e.g. "64-bit .deb", "EPUB", "Download"
    url: str              # signed, expires: refresh the order to get a new one
    md5: str = ""
    size: int = 0
    gamekey: str = ""

    @property
    def filename(self):
        return urllib.parse.unquote(urllib.parse.urlsplit(self.url).path.rsplit("/", 1)[-1]) or self.machine_name


def downloads_in_order(order):
    for sub in order.get("subproducts") or []:
        for dl in sub.get("downloads") or []:
            for f in dl.get("download_struct") or []:
                url = (f.get("url") or {}).get("web")
                if not url:
                    continue  # external links / torrent-only entries
                yield Download(
                    machine_name=sub.get("machine_name", ""), human_name=sub.get("human_name", ""),
                    platform=dl.get("platform", ""), name=f.get("name", ""), url=url,
                    md5=f.get("md5") or "", size=int(f.get("file_size") or 0), gamekey=order.get("gamekey", ""))


# Best build for this machine first; Windows builds are the Wine/Proton fallback.
PLATFORM_PREFERENCE = {"Darwin": ["mac", "windows"], "Linux": ["linux", "windows"]}


def pick_build(downloads, system=None):
    """From one subproduct's downloads, the files to fetch for this OS (or [] if none fit)."""
    order = PLATFORM_PREFERENCE.get(system or platform.system(), ["windows"])
    for plat in order:
        files = [d for d in downloads if d.platform == plat]
        if files:
            # Several builds for one platform (e.g. .deb, .rpm, .tar.gz; 32/64-bit):
            # prefer archives we can unpack without root, 64-bit over 32-bit.
            rank = lambda d: (
                0 if re.search(r"\.(tar\.\w+|tgz|zip|dmg|AppImage|sh)$", d.filename, re.I) else 1,
                0 if re.search(r"64", d.name + d.filename) else 1)
            return [sorted(files, key=rank)[0]]
    return []


def fetch(download, dest_dir, opener=None):
    """Download one file into dest_dir, resuming a partial file and checking md5."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / download.filename
    if out.exists() and download.md5 and md5sum(out) == download.md5:
        return out
    part = out.with_name(out.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    opener = opener or urllib.request.build_opener()
    with opener.open(urllib.request.Request(download.url, headers=headers), timeout=120) as resp:
        mode = "ab" if have and getattr(resp, "status", 200) == 206 else "wb"
        with open(part, mode) as f:
            shutil.copyfileobj(resp, f, 1 << 20)
    if download.md5 and md5sum(part) != download.md5:
        part.unlink()
        raise RuntimeError(f"md5 mismatch for {download.filename}; deleted, re-run to retry")
    os.replace(part, out)
    return out


def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def should_reveal(key, policy, types, steam_owned=None):
    """Decide whether to reveal an unrevealed key under --reveal POLICY."""
    if key.revealed or key.is_expired or policy == "none":
        return False
    if types and key.key_type not in types:
        return False
    if policy == "all":
        return True
    # "needed": only keys that would actually be used — never a Steam key for
    # something the Steam account already owns (keep that one giftable). If
    # ownership is unknown (steam_owned is None), leave Steam keys alone.
    if key.steam_app_id or key.key_type == "steam":
        return steam_owned is not None and key.steam_app_id not in steam_owned
    return True


def mask(value):
    if not value:
        return "(unrevealed)"
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "…"


class KeyStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "keys.json"
        self.keys = {}
        if self.path.exists():
            for d in json.loads(self.path.read_text()):
                k = Key(**d)
                self.keys[k.id] = k

    def merge(self, key):
        old = self.keys.get(key.id)
        if old and old.value and not key.value:
            key.value = old.value  # never forget a key we already extracted
        self.keys[key.id] = key

    def save(self):
        write_private(self.path, json.dumps([asdict(k) for k in self.keys.values()], indent=1))


def orders_dir():
    d = data_dir() / "orders"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def cached_orders(directory=None):
    """Orders saved by the last extract (they hold keys and signed download URLs)."""
    return [json.loads(p.read_text()) for p in sorted(Path(directory or orders_dir()).glob("*.json"))]


def extract(client, store, policy="none", types=(), steam_owned=None, log=print, save_orders=None):
    gamekeys = client.gamekeys()
    log(f"{len(gamekeys)} orders")
    revealed_now = 0
    for i, gk in enumerate(gamekeys, 1):
        order = client.order(gk)
        if save_orders:
            write_private(Path(save_orders) / f"{gk}.json", json.dumps(order))
        for key in keys_in_order(order):
            known = store.keys.get(key.id)
            if known and known.value and not key.value:
                key.value = known.value
            if should_reveal(key, policy, types, steam_owned):
                key.value = client.reveal(key)
                revealed_now += 1
                log(f"  revealed {key.human_name} ({key.key_type}) {mask(key.value)}")
            store.merge(key)
        if i % 20 == 0:
            store.save()  # don't lose reveals if interrupted
    store.save()
    total = len(store.keys)
    have = sum(1 for k in store.keys.values() if k.revealed)
    log(f"{total} keys, {have} extracted ({revealed_now} revealed this run), "
        f"{total - have} still unrevealed -> {store.path}")
    return store


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--keystore")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--cookie-file")
    e.add_argument("--reveal", choices=("none", "needed", "all"), default="none",
                   help="reveal unrevealed keys (irreversible: they can no longer be gifted)")
    e.add_argument("--type", action="append", default=[], help="only these key types (e.g. steam)")
    e.add_argument("--steam-owned", help="file of owned Steam app ids, one per line; 'needed' skips those")
    k = sub.add_parser("keys")
    k.add_argument("--type", action="append", default=[])
    k.add_argument("--show", action="store_true", help="print full key values")
    args = p.parse_args(argv)
    store = KeyStore(args.keystore)

    if args.cmd == "extract":
        owned = None
        if args.steam_owned:
            owned = frozenset(int(x) for x in Path(args.steam_owned).read_text().split() if x.isdigit())
        extract(Humble(session_cookie(args.cookie_file)), store, args.reveal, set(args.type), owned,
                save_orders=orders_dir())
    else:
        for key in sorted(store.keys.values(), key=lambda k: (k.bundle, k.human_name)):
            if args.type and key.key_type not in args.type:
                continue
            value = (key.value or "(unrevealed)") if args.show else mask(key.value)
            print(f"{key.key_type:10} {value:24} {key.human_name}  [{key.bundle}]")


if __name__ == "__main__":
    main()
