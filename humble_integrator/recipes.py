"""Recipes: how each Humble item gets from a purchase into Steam.

Every item resolves to exactly one recipe, most specific first:

  1. an item recipe   recipes/items/<vendor>/<id>.toml  (matched by Humble machine_name)
  2. a vendor recipe  recipes/vendors/<vendor>.toml     (matched by developer/publisher)
  3. a generic route  recipes/generic/<route>.toml      (matched by delivery method)

Anything that matches none of them is reported as uncovered.

    python3 -I humble_integrator/recipes.py check  [--recipes DIR]
    python3 -I humble_integrator/recipes.py cover  --census census.jsonl [--recipes DIR]
    python3 -I humble_integrator/recipes.py plan   --platform macos|linux ID...
"""

import argparse
import json
import re
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "recipes"
KINDS = {"app", "addon", "content", "document", "service", "route"}
PLATFORMS = ("macos", "linux")


class RecipeError(Exception):
    pass


@dataclass
class Recipe:
    id: str
    layer: str  # item | vendor | generic
    path: Path
    data: dict
    requires: list = field(default_factory=list)

    @property
    def kind(self):
        return self.data.get("kind", "route" if self.layer != "item" else None)

    @property
    def vendor(self):
        return self.data.get("vendor") or (self.id if self.layer == "vendor" else None)


@dataclass
class Book:
    recipes: dict
    by_machine_name: dict
    vendors: list
    generics: list

    def get(self, rid):
        try:
            return self.recipes[rid]
        except KeyError:
            raise RecipeError(f"unknown recipe {rid!r}") from None

    def find(self, name):
        """Look up a recipe by full id, Humble machine_name, or unambiguous short id."""
        if name in self.recipes:
            return self.recipes[name]
        if name in self.by_machine_name:
            return self.by_machine_name[name]
        matches = [r for rid, r in self.recipes.items() if rid.rsplit("/", 1)[-1] == name]
        if len(matches) == 1:
            return matches[0]
        raise RecipeError(f"{name!r} matches {len(matches)} recipes" if matches else f"unknown recipe {name!r}")

    def resolve(self, item, bundle=None):
        """Return (recipe, layer) for a census item, or (None, None).

        `bundle` (the census bundle record) lets vendor recipes claim items
        that only the bundle identifies, like the parts of a 3D-print pack."""
        if "hb-steam" in (item.get("delivery") or []):  # a Steam key is best used on Steam
            steam_route = self._steam_route()
            if steam_route:
                return steam_route, "generic"
        r = self.by_machine_name.get(item["machine_name"])
        if r:
            return r, "item"
        for g in self.generics:  # routes that outrank vendors (e.g. Humble's own coupons)
            if g.data.get("before_vendors") and self._generic_matches(g, item):
                return g, "generic"
        claims = self.vendor_claims(item, bundle)
        if claims:
            return claims[0][0], "vendor"
        delivery = set(item.get("delivery") or [])
        if not delivery and (bundle or {}).get("drm_free"):
            delivery = {"hb-drmfree"}
        content = item.get("content_type")
        for g in self.generics:
            if g.data.get("before_vendors"):
                continue
            m = g.data.get("match", {})
            if any(re.search(p, item.get("machine_name", "")) for p in m.get("machine_names", [])):
                return g, "generic"
            if m.get("delivery") and not delivery & set(m["delivery"]):
                continue
            if m.get("content_types") and content not in m["content_types"]:
                continue
            if m.get("delivery") or m.get("content_types"):
                return g, "generic"
        return None, None

    def _steam_route(self):
        return next((g for g in self.generics if "hb-steam" in g.data.get("match", {}).get("delivery", [])), None)

    @staticmethod
    def _generic_matches(g, item):
        """Name-based generic rules: machine_names (regexes) or developers."""
        m = g.data.get("match", {})
        if any(re.search(p, item.get("machine_name", "")) for p in m.get("machine_names", [])):
            return True
        people = {n.lower() for n in (item.get("developers") or []) + (item.get("publishers") or []) if n}
        return bool(people & {n.lower() for n in m.get("developers", [])})

    # Where a key is redeemed is decided by the bundle, not by who made the item:
    # bundle-level rules outrank developer rules; ties go to file order.
    RULE_RANK = {"redeem_text": 0, "bundle_slugs": 1, "developers": 2}

    def _index(self):
        """Compiled match rules, a developer -> vendors index, and a per-bundle cache.

        Bundle-level rules depend only on the bundle, so they run once per bundle
        instead of once per item (the census has ~30k items over ~1k bundles)."""
        if getattr(self, "_idx", None) is None:
            rules, by_dev = [], {}
            for i, v in enumerate(self.vendors):
                m = v.data.get("match", {})
                rules.append(([re.compile(p, re.I) for p in m.get("redeem_text", [])],
                              [re.compile(p) for p in m.get("bundle_slugs", [])]))
                for n in m.get("developers", []):
                    by_dev.setdefault(n.lower(), set()).add(i)
            self._idx = (rules, by_dev, {})
        return self._idx

    def _bundle_hits(self, bundle):
        rules, _, cache = self._index()
        slug = (bundle or {}).get("slug", "")
        text = (bundle or {}).get("redeem_text", "")
        key = (slug, text)
        if key not in cache:
            hits = {}
            for i, (text_res, slug_res) in enumerate(rules):
                if text and any(r.search(text) for r in text_res):
                    hits[i] = "redeem_text"
                elif slug and any(r.search(slug) for r in slug_res):
                    hits[i] = "bundle_slugs"
            cache[key] = hits
        return cache[key]

    def _claims(self, item, bundle):
        """[(vendor index, rule, developer also matches)], strongest first."""
        _, by_dev, _ = self._index()
        people = {n.lower() for n in item.get("developers", []) + item.get("publishers", []) if n}
        devs = set().union(*(by_dev.get(n, ()) for n in people)) if people else set()
        hits = self._bundle_hits(bundle)
        claims = [(i, rule, i in devs) for i, rule in hits.items()]
        claims += [(i, "developers", True) for i in devs if i not in hits]
        # Ties go to the vendor whose developer rule also names the item's maker,
        # then to file order (vendor index).
        claims.sort(key=lambda c: (self.RULE_RANK[c[1]], not c[2], c[0]))
        return claims

    def vendor_claims(self, item, bundle=None):
        """Every (vendor recipe, strongest rule) that claims this item, strongest first.

        Ties on the same bundle-level rule (a bundle "redeemable through A and B
        websites") go to the vendor whose developer rule also names the item's maker."""
        return [(self.vendors[i], rule) for i, rule, _ in self._claims(item, bundle)]

    def claims_tied(self, item, bundle=None):
        """True when the two strongest vendor claims can't be told apart."""
        claims = self._claims(item, bundle)
        return len(claims) >= 2 and claims[0][1:] == claims[1][1:]

    def resolve_key(self, key):
        """Return (recipe, layer) for an extracted Humble key (humble.Key or its dict)."""
        k = key if isinstance(key, dict) else vars(key)
        if k.get("steam_app_id") or k.get("key_type") == "steam":
            steam_route = self._steam_route()
            if steam_route:
                return steam_route, "generic"
        r = self.by_machine_name.get(k.get("machine_name"))
        if r:
            return r, "item"
        text = " ".join(str(k.get(f) or "") for f in ("instructions", "human_name", "machine_name")).lower()
        # The bundle a key came from often names the vendor when the key itself doesn't
        # (a Packt key is just called after its course).
        bundle_slugs = {s for s in ((k.get("bundle_machine_name") or "").replace("_", "-"),
                                    re.sub(r"[^a-z0-9]+", "-", (k.get("bundle") or "").lower()).strip("-")) if s}
        # Evidence on the key itself beats the bundle it came from: a vendor's
        # domain in the instructions, then its key-name patterns, then bundle slugs.
        rules = (
            lambda m: any(d.lower() in text for d in m.get("domains", [])),
            lambda m: any(re.search(p, text) for p in m.get("key_names", [])),
            lambda m: any(re.search(p, s) for p in m.get("bundle_slugs", []) for s in bundle_slugs),
        )
        for rule in rules:
            for v in self.vendors:
                if rule(v.data.get("match", {})):
                    return v, "vendor"
        for g in self.generics:
            m = g.data.get("match", {})
            if k.get("key_type") in m.get("key_types", []) or (k.get("steam_app_id") and m.get("steam_app_id")):
                return g, "generic"
        return None, None


def load(root=ROOT):
    recipes, by_mn, vendors, generics = {}, {}, [], []
    for layer, sub in (("generic", "generic"), ("vendor", "vendors"), ("item", "items")):
        for path in sorted((root / sub).rglob("*.toml")):
            rel = path.relative_to(root / sub).with_suffix("")
            rid = rel.as_posix() if layer == "item" else f"{sub}/{rel.as_posix()}"
            try:
                data = tomllib.loads(path.read_text())
            except tomllib.TOMLDecodeError as e:
                raise RecipeError(f"{path}: {e}") from None
            r = Recipe(rid, layer, path, data, list(data.get("requires", [])))
            recipes[rid] = r
            if layer == "item":
                for mn in data.get("machine_names", []):
                    if mn in by_mn:
                        raise RecipeError(f"{mn} claimed by both {by_mn[mn].id} and {rid}")
                    by_mn[mn] = r
            elif layer == "vendor":
                vendors.append(r)
            else:
                generics.append(r)
    generics.sort(key=lambda g: g.data.get("priority", 100))
    return Book(recipes, by_mn, vendors, generics)


def check(book):
    """Return a list of problems; empty means the recipe book is consistent."""
    problems = []
    for r in book.recipes.values():
        d = r.data
        where = r.path.name
        if d.get("schema") != 1:
            problems.append(f"{r.id}: schema must be 1 ({where})")
        if not d.get("name"):
            problems.append(f"{r.id}: missing name")
        if r.layer == "item":
            if r.kind not in KINDS - {"route"}:
                problems.append(f"{r.id}: kind must be one of {sorted(KINDS - {'route'})}")
            if not d.get("machine_names"):
                problems.append(f"{r.id}: no machine_names, so no Humble item can reach it")
            v = d.get("vendor")
            if v and f"vendors/{v}" not in book.recipes:
                problems.append(f"{r.id}: vendor {v!r} has no recipes/vendors/{v}.toml")
            launch = d.get("launch", {})
            if d.get("steam", {}).get("shortcut") and not (launch.get("exe") or launch.get("find")):
                problems.append(f"{r.id}: steam.shortcut needs launch.exe or launch.find")
            for step in d.get("install", {}).get("then", []):
                if step.startswith("run:") and step[4:] not in book.recipes:
                    problems.append(f"{r.id}: then-step {step!r} names an unknown recipe")
        for dep in r.requires + list(d.get("recommends", [])):
            if dep not in book.recipes:
                problems.append(f"{r.id}: requires/recommends unknown recipe {dep!r}")
    try:
        order(book, list(book.recipes), skip_unknown=True)  # unknown deps already reported
    except RecipeError as e:
        problems.append(str(e))
    return problems


def order(book, ids, skip_unknown=False):
    """Dependency-first install order for the given recipe ids (and what they require).

    `recommends` only orders: a recommended recipe goes first when it is part
    of the plan anyway, but is never pulled in on its own.
    """
    known = lambda rid: not skip_unknown or rid in book.recipes
    plan, todo = set(), [i for i in ids if known(i)]
    while todo:
        rid = todo.pop()
        if rid not in plan:
            plan.add(rid)
            todo.extend(d for d in book.get(rid).requires if known(d))
    out, state = [], {}

    def visit(rid, chain):
        if state.get(rid) == "done":
            return
        if state.get(rid) == "active":
            raise RecipeError("dependency cycle: " + " -> ".join(chain + [rid]))
        state[rid] = "active"
        r = book.get(rid)
        deps = [d for d in r.requires if known(d)]
        deps += [d for d in r.data.get("recommends", []) if d in plan and d not in deps]
        for dep in deps:
            visit(dep, chain + [rid])
        state[rid] = "done"
        out.append(rid)

    for rid in ids:
        if known(rid):
            visit(rid, [])
    return out


def conflicts(book, census_path):
    """Items claimed by more than one vendor recipe: {(vendor ids...): [example item refs]}."""
    out = defaultdict(list)
    with open(census_path) as f:
        for line in f:
            bundle = json.loads(line)
            for item in bundle["items"]:
                if item["machine_name"] in book.by_machine_name:
                    continue
                claims = book.vendor_claims(item, bundle)
                if book.claims_tied(item, bundle):
                    key = tuple(f"{v.id}({rule})" for v, rule in claims)
                    out[key].append(f"{bundle['category']}/{bundle['slug']}:{item['machine_name']}")
    return out


def coverage(book, census_path):
    by_layer, by_route, uncovered = Counter(), Counter(), defaultdict(list)
    total = 0
    with open(census_path) as f:
        for line in f:
            bundle = json.loads(line)
            for item in bundle["items"]:
                total += 1
                recipe, layer = book.resolve(item, bundle)
                if recipe is None:
                    key = ",".join(item.get("delivery") or ["-"]) + " | " + (
                        (item.get("developers") or item.get("publishers") or ["?"])[0] or "?")
                    uncovered[key].append(f"{bundle['category']}/{bundle['slug']}:{item['machine_name']}")
                    continue
                by_layer[layer] += 1
                by_route[recipe.id] += 1
    return total, by_layer, by_route, uncovered


def platform_view(book, rid, platform):
    """Merge a recipe with its vendor's and its own [platforms.<platform>] tables."""
    r = book.get(rid)
    merged = {}
    if r.vendor and f"vendors/{r.vendor}" in book.recipes:
        v = book.get(f"vendors/{r.vendor}").data
        merged.update(v.get("platforms", {}).get(platform, {}))
    merged.update(r.data.get("platforms", {}).get(platform, {}))
    return merged


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--recipes", type=Path, default=ROOT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    c = sub.add_parser("cover")
    c.add_argument("--census", required=True)
    c.add_argument("--top", type=int, default=40)
    c.add_argument("--conflicts", action="store_true", help="list items more than one vendor claims")
    pl = sub.add_parser("plan")
    pl.add_argument("--platform", choices=PLATFORMS, required=True)
    pl.add_argument("ids", nargs="+")
    args = p.parse_args(argv)
    book = load(args.recipes)

    if args.cmd == "check":
        problems = check(book)
        for msg in problems:
            print(msg)
        print(f"{len(book.recipes)} recipes, {len(problems)} problems", file=sys.stderr)
        sys.exit(1 if problems else 0)

    if args.cmd == "cover" and args.conflicts:
        found = conflicts(book, args.census)
        for key, items in sorted(found.items(), key=lambda kv: -len(kv[1])):
            print(f"{len(items):6d}  {' vs '.join(key)}   e.g. {items[0]}")
        print(f"{sum(len(v) for v in found.values())} items claimed by more than one vendor", file=sys.stderr)
        return

    if args.cmd == "cover":
        total, by_layer, by_route, uncovered = coverage(book, args.census)
        n_unc = sum(len(v) for v in uncovered.values())
        print(f"{total} items: " + ", ".join(f"{k} {v}" for k, v in by_layer.most_common())
              + f", uncovered {n_unc}")
        for rid, n in by_route.most_common(args.top):
            print(f"  {n:6d}  {rid}")
        if uncovered:
            print("uncovered, by delivery | developer:")
            for key, items in sorted(uncovered.items(), key=lambda kv: -len(kv[1]))[:args.top]:
                print(f"  {len(items):6d}  {key}   e.g. {items[0]}")
        return

    if args.cmd == "plan":
        ids = [book.find(i).id for i in args.ids]
        for rid in order(book, ids):
            r = book.get(rid)
            print(f"{rid}  [{r.kind}]  {r.data.get('name')}")
            for k, v in platform_view(book, rid, args.platform).items():
                print(f"    {args.platform}.{k} = {v}")


if __name__ == "__main__":
    main()
