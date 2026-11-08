"""A bundle's own Humble page, live or as archived while it was on sale: the bundle's
artwork and pictures of its items, for the bundle's Steam entry (Humble's order API
only has 70px icons).

Bundles since about 2017 each have a page, listed in data/bundle-pages.tsv (built from
the census: `census.py index`). Older ones were shown on a landing page (/books,
/software, the front page); the Internet Archive's capture of it from the day the
bundle was bought is used when its title or data names the bundle.

    python3 -I humble_integrator/pages.py MACHINE_NAME "HUMAN NAME" [BOUGHT]
"""

import html as htmllib
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import census  # noqa: E402
import humble  # noqa: E402
import steam  # noqa: E402

INDEX = Path(__file__).resolve().parent.parent / "data" / "bundle-pages.tsv"
HUMBLE = "https://www.humblebundle.com"
ARCHIVE = "https://web.archive.org/web"
# Where a bundle without a page of its own was shown, by the end of its machine name.
LANDING = {"bookbundle": ["books"], "softwarebundle": ["software"]}
OTHER_LANDING = ["", "weekly"]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _url(u):
    u = htmllib.unescape(u or "").strip()
    u = "https:" + u if u.startswith("//") else u
    return u if u.startswith(("https://", "http://")) else ""


def index(path=INDEX):
    """machine name -> (category, slug, archived capture) for the census's bundles."""
    try:
        rows = [line.split("\t") for line in Path(path).read_text().splitlines()]
    except OSError:
        return {}
    return {r[0]: tuple(r[1:4]) for r in rows if len(r) >= 4}


def titled(title, name):
    """Whether TITLE starts with NAME as whole words ("Humble Indie Bundle 1" isn't "... 10")."""
    want, acc = _norm(name), ""
    for word in re.findall(r"[a-z0-9]+", htmllib.unescape(title).lower()):
        acc += word
        if acc == want:
            return True
        if not want.startswith(acc):
            return False
    return False


def is_page_of(html, bundle):
    machine = bundle.get("machine_name") or ""
    if machine and re.search(rf"(?<!\w){re.escape(machine)}(?!\w)", html):
        return True
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return bool(title and bundle.get("human_name") and titled(title.group(1), bundle["human_name"]))


def _json_value(html, key):
    m = re.search(rf'"{re.escape(key)}"\s*:\s*("(?:[^"\\]|\\.)*")', html)
    return _url(json.loads(m.group(1))) if m else ""


def _meta(html, name):
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(rf"""\b(?:property|name)\s*=\s*["']{re.escape(name)}["']""", tag, re.I):
            content = re.search(r"""\bcontent\s*=\s*["']([^"']*)["']""", tag, re.I)
            if content:
                return _url(content.group(1))
    return ""


def page_art(html):
    """{"hero", "wide", "logo": url, "items": [[name, picture url]]} from a bundle page. The
    hero is the bundle's tile without text, the wide one its share image, the logo its
    transparent title; items are in page order."""
    wide = _meta(html, "og:image")
    art = {"hero": _json_value(html, "high_res_tile_image") or wide, "wide": wide,
           "logo": _json_value(html, "tile_logo"), "items": []}
    for item in ((census.extract_bundle_data(html) or {}).get("tier_item_data") or {}).values():
        paths = item.get("resolved_paths") or {}
        picture = next((_url(p) for p in (paths.get("front_page_art_imgix_retina"), paths.get("preview_image"),
                                           item.get("preview_image")) if _url(p)), "")
        if item.get("human_name") and picture:
            art["items"].append([item["human_name"], picture])
    m = re.search(r'"display_items"\s*:\s*', html)
    if not art["items"] and m:  # about 2017-2020: in the page's script data
        try:
            items = json.JSONDecoder().raw_decode(html, m.end())[0]
        except ValueError:
            items = {}
        for item in items.values() if isinstance(items, dict) else []:
            if isinstance(item, dict) and item.get("human_name") and _url(item.get("preview_image")):
                art["items"].append([item["human_name"], _url(item["preview_image"])])
    if not art["items"]:  # about 2013-2016: a pane per item, with its picture and then its name
        for pane in re.split(r"""class=['"]game-info-pane\b""", html)[1:]:
            picture = re.search(r"""<img\b[^>]*?\b(?:data-src|src)=["'](https?://[^"']+\.(?:jpe?g|png))["']""", pane)
            name = re.search(r"<h2[^>]*>(.*?)</h2>", pane, re.S)
            if picture and name:
                art["items"].append([htmllib.unescape(re.sub(r"<[^>]+>|\s+", " ", name.group(1))).strip(),
                                     picture.group(1)])
    return art


def picture(url, page=""):
    """(bytes, ".png" or ".jpg") of an image, or None. Images gone from Humble's servers are
    taken from the archive's copy, as of the PAGE's capture."""
    snap = re.match(r"(https://web\.archive\.org/web/\d+)id_/", page or "")
    for u in [url] + ([f"{snap.group(1)}im_/{url}"] if snap else []):
        data = (_archive_get if u.startswith(ARCHIVE) else _live_get)(u) or b""
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, ".png"
        if data[:2] == b"\xff\xd8":
            return data, ".jpg"
    return None


def archived(url, day, get):
    """The archive's capture of URL closest to DAY (YYYYMMDD), as originally served, or None."""
    found = get(f"https://archive.org/wayback/available?url={urllib.parse.quote(url)}&timestamp={day}")
    snap = (json.loads(found or b"{}").get("archived_snapshots") or {}).get("closest") or {}
    return f"{ARCHIVE}/{snap['timestamp']}id_/{url}" if snap.get("available") and snap.get("timestamp") else None


def candidates(bundle, pages=None):
    """Where BUNDLE's page may be: (url, archived?) pairs, or (landing url, day) to look up."""
    machine = bundle.get("machine_name") or ""
    pages = index() if pages is None else pages
    if machine in pages:
        category, slug, snapshot = pages[machine]
        yield f"{HUMBLE}/{category}/{slug}", None  # many ended bundles' pages are still up
        yield f"{ARCHIVE}/{snapshot}id_/{HUMBLE}/{category}/{slug}", None
    day = re.sub(r"\D", "", bundle.get("bought") or "")[:8]
    if len(day) == 8:
        for path in LANDING.get(machine.rsplit("_", 1)[-1], OTHER_LANDING):
            yield f"{HUMBLE}/{path}", day


def _archive_get(url):
    return census.http_get(url, attempts=4, ua=steam.UA)  # throttled, with backoff


def _live_get(url):
    try:
        return steam.http_get(url)
    except Exception:
        return None


def find(bundle, cache_dir=None, pages=None, live=_live_get, archive=_archive_get):
    """Artwork URLs for BUNDLE ({"machine_name", "human_name", "bought"}): page_art() of its
    page, or {} when none is found. Kept in CACHE_DIR, misses too, so each bundle is looked
    up once; a network failure isn't kept."""
    key = bundle.get("machine_name") or _norm(bundle.get("human_name"))
    cache = Path(cache_dir or humble.data_dir() / "pages") / f"{re.sub(r'[^a-z0-9_-]', '_', key.lower())}.json"
    try:
        return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    best = {}
    for url, day in candidates(bundle, pages):
        if day:
            url = archived(url, day, archive)
            body = archive(url) if url else None
        else:
            body = (archive if url.startswith(ARCHIVE) else live)(url)
        html = (body or b"").decode("utf-8", "replace")
        if html and is_page_of(html, bundle):
            art = page_art(html)
            if art["items"] and (art["hero"] or art["wide"]):
                best = dict(art, page=url)
                break
            best = best or dict(art, page=url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(best))
    return best


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        sys.exit(__doc__)
    found = find({"machine_name": args[0], "human_name": args[1], "bought": args[2] if len(args) > 2 else ""})
    print(json.dumps(found, indent=1))


if __name__ == "__main__":
    main()
