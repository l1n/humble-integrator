"""Census of every Humble bundle, rebuilt from Wayback Machine snapshots.

Humble blanks a bundle's page once it ends, but archived captures taken while
it was live still carry the page's `tier_item_data` JSON: every item, its tier,
and how it is delivered (Steam key, DRM-free download, other key, ...).

    python3 -I humble_integrator/census.py list  --cache DIR
    python3 -I humble_integrator/census.py fetch --cache DIR [--workers N]
    python3 -I humble_integrator/census.py parse --cache DIR --out census.jsonl

`list` and `fetch` are resumable: everything fetched is cached under DIR.
"""

import argparse
import concurrent.futures
import gzip
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "humble-integrator" / "wayback"

CATEGORIES = ("games", "books", "software")
CDX = "https://web.archive.org/cdx/search/cdx"
UA = "humble-integrator-census/0.1 (one-off research crawl; polite, cached)"
SLUG_RE = re.compile(r"^com,humblebundle\)/(games|books|software)/([a-z0-9][a-z0-9-]*)$")
# Candidate snapshots tried per bundle, earliest first: bundles are usually
# captured while live, and later captures tend to be the "bundle is over" page.
MAX_CANDIDATES = 6


class ArchiveBusy(Exception):
    pass


class Throttle:
    """At most one request start per `interval` seconds across all worker threads.

    archive.org starts refusing connections when crawled too fast, and a
    refusal backs the whole crawl off (`penalize`)."""

    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        time.sleep(delay)

    def penalize(self, seconds):
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


THROTTLE = Throttle(1.0)


def http_get(url, *, attempts=8, base_delay=5.0):
    """GET with backoff on archive.org's 429/5xx and 'Temporarily Offline' pages."""
    for attempt in range(attempts):
        THROTTLE.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            body = maybe_decompress(body)
            if b"Internet Archive: Temporarily Offline" in body[:2000]:
                raise ArchiveBusy("temporarily offline")
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, ArchiveBusy) as e:
            err = e
        delay = base_delay * (2 ** attempt)
        if "refused" in str(err) or "429" in str(err):
            THROTTLE.penalize(60)  # the archive is pushing back: everyone pauses
        print(f"  retry {attempt + 1}/{attempts} in {delay:.0f}s ({err}): {url[:120]}", file=sys.stderr)
        time.sleep(min(delay, 600))
    raise RuntimeError(f"giving up on {url}")


def maybe_decompress(body):
    # `id_` captures are the original bytes, which are sometimes still gzipped.
    if body[:2] == b"\x1f\x8b":
        try:
            return zlib.decompress(body, 47)
        except zlib.error:
            pass
    return body


# ---------------------------------------------------------------- list

def cdx_pages(category):
    url = f"{CDX}?" + urllib.parse.urlencode(
        {"url": f"humblebundle.com/{category}/", "matchType": "prefix", "showNumPages": "true"})
    return int(http_get(url).strip())


def cdx_page(category, page):
    query = urllib.parse.urlencode([
        ("url", f"humblebundle.com/{category}/"), ("matchType", "prefix"),
        ("fl", "urlkey,timestamp"), ("filter", "statuscode:200"),
        ("filter", "mimetype:text/html"), ("page", str(page)),
    ])
    return http_get(f"{CDX}?{query}").decode("utf-8", "replace")


def cmd_list(args):
    cache = Path(args.cache)
    (cache / "cdx").mkdir(parents=True, exist_ok=True)
    snapshots = {}  # (category, slug) -> set of timestamps
    for category in CATEGORIES:
        n = cdx_pages(category)
        print(f"{category}: {n} index pages", file=sys.stderr)
        for page in range(n):
            path = cache / "cdx" / f"{category}-{page:03d}.txt"
            if not path.exists():
                path.write_text(cdx_page(category, page))
                time.sleep(1)
            for line in path.read_text().splitlines():
                parts = line.split()
                if len(parts) != 2:
                    continue
                m = SLUG_RE.match(parts[0].split("?", 1)[0])
                if m:
                    snapshots.setdefault(m.groups(), set()).add(parts[1])
    out = [{"category": c, "slug": s, "timestamps": sorted(ts)} for (c, s), ts in sorted(snapshots.items())]
    (cache / "bundles.json").write_text(json.dumps(out, indent=0))
    print(f"{len(out)} bundle pages listed", file=sys.stderr)


# ---------------------------------------------------------------- fetch

def candidates(timestamps):
    if len(timestamps) <= MAX_CANDIDATES:
        return timestamps
    # The two earliest, then the rest spread evenly up to the latest.
    rest = timestamps[2:]
    n = MAX_CANDIDATES - 2
    return timestamps[:2] + [rest[round(i * (len(rest) - 1) / (n - 1))] for i in range(n)]


def page_path(cache, category, slug):
    return cache / "pages" / category / f"{slug}.html.gz"  # gzipped: ~6x smaller


def fetch_one(cache, entry):
    category, slug = entry["category"], entry["slug"]
    out = page_path(cache, category, slug)
    miss = out.with_name(f"{slug}.miss")
    if out.exists() or miss.exists():
        return "cached"
    for ts in candidates(entry["timestamps"]):
        url = f"https://web.archive.org/web/{ts}id_/https://www.humblebundle.com/{category}/{slug}"
        body = http_get(url)
        if body and extract_bundle_data(body.decode("utf-8", "replace")):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(gzip.compress(body))
            out.with_name(f"{slug}.ts").write_text(ts)
            return "ok"
        time.sleep(1)
    miss.parent.mkdir(parents=True, exist_ok=True)
    miss.write_text("\n".join(entry["timestamps"]))
    return "miss"


def cmd_fetch(args):
    cache = Path(args.cache)
    entries = json.loads((cache / "bundles.json").read_text())
    if args.only:
        entries = [e for e in entries if e["category"] in args.only]
    if args.skip_census and census_files(args.skip_census):
        # Bundles already parsed into a census don't need fetching again.
        known = {(b["category"], b["slug"]) for b in iter_census(args.skip_census)}
        entries = [e for e in entries if (e["category"], e["slug"]) not in known]
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(fetch_one, cache, e): e for e in entries}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            e = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # keep going; a rerun retries failures
                result = "error"
                print(f"error {e['category']}/{e['slug']}: {exc}", file=sys.stderr)
            counts[result] = counts.get(result, 0) + 1
            if i % 25 == 0 or i == len(entries):
                print(f"{i}/{len(entries)} {counts}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- parse

JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def extract_bundle_data(html):
    for blob in JSON_SCRIPT_RE.findall(html):
        if "tier_item_data" not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        bundle = find_key(data, "tier_item_data")
        if bundle and bundle.get("tier_item_data"):
            return bundle
    return extract_legacy_bundle(html)


TIER_HEADLINE_RE = re.compile(r'class="dd-header-headline"[^>]*>(.*?)</', re.S)
SLIDEOUT_RE = re.compile(r'data-slideout="([^"]+)"')


def extract_legacy_bundle(html):
    """Bundle pages from about 2017-2020 keep items in bundleVars.slideout_data and
    tiers only in the HTML; normalize them to the newer tier_item_data shape."""
    for blob in JSON_SCRIPT_RE.findall(html):
        if "slideout_data" not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        bv = find_key(data, "slideout_data")
        items = ((bv or {}).get("slideout_data") or {}).get("display_items") or {}
        # Older pages list the charities as tiles too; they carry no delivery icon.
        items = {k: v for k, v in items.items()
                 if not (v.get("availability_icons") is None and re.search(
                     r"Giving Fund|501\(c\)\(3\)|charit|donation", v.get("description_text") or "", re.I))}
        if not items:
            continue
        # Tiers: each "Pay $X or more to unlock!" heading, then the item boxes under it.
        tier_order, tier_display = [], {}
        heads = list(TIER_HEADLINE_RE.finditer(html))
        for i, h in enumerate(heads):
            section = html[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(html)]
            names = list(dict.fromkeys(n for n in SLIDEOUT_RE.findall(section) if n in items))
            if names:
                tier = f"tier{i}"
                tier_order.append(tier)
                tier_display[tier] = {"header": strip_html(h.group(1)), "tier_item_machine_names": names}
        # Redemption wording lives in the page text, not in a JSON field.
        text = strip_html(re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S))
        # Drop the per-item badges ("Redeem for Windows Mac not available ... MSRP: $29.99").
        text = re.sub(r"Redeem for(?: (?:Windows|Mac|Linux|Android|Steam|Key|not available|Download))+|"
                      r"Locked content|MSRP: \S+", " ", text)
        redeem = " ".join(s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                          if re.search(r"redeem|DRM|download|key", s, re.I))[:2000]
        return {
            "machine_name": bv.get("product_machine_name"),
            "basic_data": {"human_name": bv.get("product_human_name"), "description": redeem},
            "tier_order": tier_order,
            "tier_display_data": tier_display,
            "tier_item_data": items,
        }
    return None


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def item_record(machine_name, item, tiers_by_item):
    icons = item.get("availability_icons") or {}
    return {
        "machine_name": machine_name,
        "human_name": item.get("human_name"),
        "content_type": item.get("item_content_type"),
        "tiers": tiers_by_item.get(machine_name, []),
        "delivery": icons.get("delivery_icons") or [],
        "platforms": icons.get("platform_icons") or [],
        "delivery_to_platform": {
            k: v.get("available", []) for k, v in (icons.get("delivery_to_platform") or {}).items()},
        # Newer pages spell these "developer-name", older ones "developer_name".
        "developers": [n for d in item.get("developers") or []
                       if (n := d.get("developer-name") or d.get("developer_name"))],
        "publishers": [n for p in item.get("publishers") or []
                       if (n := p.get("publisher-name") or p.get("publisher_name"))],
        "description": strip_html(item.get("description_text"))[:600],
    }


def bundle_record(category, slug, ts, bundle, html=""):
    basic = bundle.get("basic_data") or {}
    # Pages carry "has_any_keys": false for bundles that are all Humble downloads.
    m = re.search(r'"has_any_keys":\s*(true|false)', html)
    has_keys = None if not m else m.group(1) == "true"
    tiers_by_item = {}
    for tier in bundle.get("tier_order") or []:
        for name in (bundle.get("tier_display_data") or {}).get(tier, {}).get("tier_item_machine_names") or []:
            tiers_by_item.setdefault(name, []).append(tier)
    charity = set((bundle.get("charity_data") or {}).get("charity_items") or {})
    items = [item_record(k, v, tiers_by_item)
             for k, v in (bundle.get("tier_item_data") or {}).items() if k not in charity]
    return {
        "category": category,
        "slug": slug,
        "snapshot": ts,
        "machine_name": bundle.get("machine_name"),
        "human_name": basic.get("human_name"),
        "start": basic.get("start_time|datetime"),
        "end": basic.get("end_time|datetime"),
        "single_key": "single key will be provided" in json.dumps(bundle).lower(),
        # Where keys get redeemed is usually only stated at bundle level.
        "redeem_text": strip_html(" ".join(str(basic.get(k) or "") for k in (
            "description", "detailed_marketing_blurb", "order_form_disclaimer")))[:2000],
        "account_links": basic.get("required_account_links") or [],
        # Packs whose parts carry no delivery icon say so at bundle level.
        "has_keys": has_keys,
        "drm_free": has_keys is False or bool(re.search(
            r"Redeem DRM-Free|available (?:for download |as )?DRM-?free|DRM-?free (?:download|asset)|"
            r"direct download|Download assets",
            " ".join(str(basic.get(k) or "") for k in ("description", "detailed_marketing_blurb")), re.I)),
        "items": items,
    }


def census_files(path):
    """A census is one .jsonl file or a directory of per-category .jsonl files."""
    p = Path(path)
    if p.is_dir():
        return sorted(p.glob("*.jsonl"))
    return [p] if p.exists() else []


def iter_census(path):
    for f in census_files(path):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def write_census(records, path):
    """Write {(category, slug): record}. A path without .jsonl is a directory that
    gets one file per category (keeps each file well under GitHub's size limits)."""
    p = Path(path)
    groups = {}
    for key in sorted(records):
        groups.setdefault(key[0] if p.suffix != ".jsonl" else "", []).append(records[key])
    if p.suffix != ".jsonl":
        p.mkdir(parents=True, exist_ok=True)
    for cat, recs in groups.items():
        out = p / f"{cat}.jsonl" if cat else p
        tmp = out.with_suffix(".tmp")  # readers never see a half-written census
        with open(tmp, "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        tmp.replace(out)


def cmd_parse(args):
    """Rebuild OUT from the cache, keeping records already in OUT that the cache lacks."""
    cache = Path(args.cache)
    records = {(b["category"], b["slug"]): b for b in iter_census(args.out)}
    kept, n = len(records), 0
    for path in sorted((cache / "pages").glob("*/*.html*")):
        category = path.parent.name
        slug = path.name.split(".html")[0]
        raw = path.read_bytes()
        html = (gzip.decompress(raw) if path.suffix == ".gz" else raw).decode("utf-8", "replace")
        bundle = extract_bundle_data(html)
        if not bundle:
            continue
        ts = path.with_name(f"{slug}.ts").read_text().strip()
        records[(category, slug)] = bundle_record(category, slug, ts, bundle, html)
        n += 1
    write_census(records, args.out)
    misses = len(list((cache / "pages").glob("*/*.miss")))
    print(f"{len(records)} bundles in census ({n} parsed from cache, {kept} were already there); "
          f"{misses} without item data", file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "fetch", "parse"):
        sp = sub.add_parser(name)
        sp.add_argument("--cache", default=str(DEFAULT_CACHE))
    sub.choices["fetch"].add_argument("--workers", type=int, default=3)
    sub.choices["fetch"].add_argument("--only", nargs="*", choices=CATEGORIES)
    sub.choices["fetch"].add_argument("--skip-census", help="skip bundles already in this census.jsonl")
    sub.choices["parse"].add_argument("--out", required=True)
    args = p.parse_args(argv)
    {"list": cmd_list, "fetch": cmd_fetch, "parse": cmd_parse}[args.cmd](args)


if __name__ == "__main__":
    main()
