# Recipes

A recipe says how one kind of Humble item gets from a purchase into Steam on
macOS and Linux. Every item in every Humble bundle (see `data/census.jsonl`)
resolves to exactly one recipe, most specific first:

| Layer | Path | Matched by |
|---|---|---|
| item | `items/<vendor>/<id>.toml` | the item's Humble `machine_name` (listed in `machine_names`) |
| vendor | `vendors/<vendor>.toml` | the item's developer or publisher name |
| generic | `generic/<route>.toml` | the item's delivery method and content type |

`python3 -I humble_integrator/recipes.py check` validates the book;
`... cover --census data/census.jsonl` reports what is still uncovered.

Every file starts with `schema = 1`. Unknown keys are allowed but ignored.

## Item recipes

One file per *product*, not per bundle appearance: the same product shows up
in many bundles under different machine names, so list them all.

```toml
schema = 1
name = "Dungeon Designer 3"
vendor = "profantasy"                 # must have vendors/profantasy.toml
kind = "addon"                        # app | addon | content | document | service
machine_names = ["dungeondesigner3"]  # every Humble machine_name for this product
requires = ["profantasy/cc3plus"]     # other item recipe ids, installed first
verified = false                      # true only once someone has run it end to end
notes = "Runs inside CC3+; no Steam entry of its own."

[install]
source = "vendor"          # humble | vendor | steam   (where the installer comes from)
file = "DD3*.exe"          # glob for the downloaded installer / archive
args = []                  # silent-install arguments, if known
serial = "per-product"     # none | per-product | account  (what the installer asks for)
then = ["run:profantasy/cc3plus"]   # post-install steps; run:<id> launches that app once

[launch]                   # apps only
exe = 'C:\Program Files (x86)\ProFantasy\CC3Plus\fcw32.exe'   # Windows path inside the prefix,
                           # or a path relative to the install dir for native builds
args = []

[steam]
shortcut = false           # true => add a non-Steam shortcut (needs launch.exe)
title = "Campaign Cartographer 3+"
steamgriddb = "Campaign Cartographer 3+"   # search term for artwork

[platforms.macos]          # overrides for macOS (merged over the vendor's)
runner = "crossover"
windows = "win10"          # Windows version for this product's install step

[platforms.linux]
runner = "proton"

[[sources]]                # where each fact came from
url = "https://..."
claim = "CC3+ must be opened once after each add-on install"
```

Kinds:

- **app** — something you launch; the only kind that gets a Steam shortcut.
- **addon** — installs into an app (needs `requires`); no shortcut.
- **content** — symbols, maps, assets, tokens consumed by an app or by hand.
- **document** — PDFs, ebooks, comics, audio, video: downloaded to a library folder.
- **service** — subscriptions, accounts, web apps, codes for online services: nothing to install; the recipe documents redemption only.

## Vendor recipes

Shared knowledge for one vendor: how a Humble key turns into their downloads,
and the environment their products install into.

```toml
schema = 1
name = "ProFantasy Software"
kind = "route"
[match]
developers = ["ProFantasy Software"]   # census items: matched against developer AND publisher names
domains = ["profantasy.com"]           # extracted keys: found in Humble's redemption instructions
key_names = ["profantasy"]             # extracted keys: regexes over the key's name (lowercased)
bundle_slugs = ["^map-making"]         # census items: regexes over the bundle's URL slug
redeem_text = ["ProFantasy"]           # census items: regexes over the bundle's redemption text
                                       # ("Keys redeemable on the X website"); best for marketplaces

[redeem]
where = "https://..."      # where the Humble key is redeemed
account = true             # needs an account with the vendor
steps = ["...", "..."]     # human-readable, in order
delivers = "per-product serials and a downloads page"

[prefix]                   # Windows-only vendors: one shared Wine prefix
name = "profantasy"
arch = "win64"
windows = "winxp64"
winetricks = []

[platforms.macos]
runner = "crossover"       # crossover | kegworks | native
[platforms.linux]
runner = "proton"          # proton | wine | native
```

## Generic routes

Routes that need no per-product knowledge: Steam keys, DRM-free builds,
and downloadable files. `match.delivery` lists Humble delivery icons
(`hb-steam`, `hb-drmfree`, …); `match.content_types` lists Humble
`item_content_type` values. For extracted keys, `match.key_types` lists
Humble `key_type` values and `match.steam_app_id = true` catches any key
that names a Steam app. Lower `priority` is tried first.

## Keys

`humble_integrator/humble.py extract` pulls every key out of a Humble
account into a private keystore (0600). An extracted key resolves to a
recipe like an item does: item `machine_names`, then vendor `domains` /
`key_names`, then generic `key_types`. A vendor recipe's `[redeem]` is
where that key goes next.

## Fields that grew out of wave 1 (use these, don't invent synonyms)

| Field | Meaning |
|---|---|
| `install.url` | where the installer is downloaded when it isn't a Humble download |
| `install.args` | silent-install arguments; `{key}` stands for the user's own revealed key |
| `install.driver` / `driver_notes` | installer framework for the wizard driver (`installaware`, `inno`, `nsis`, `msi`, …) |
| `launch.find` | glob for the exe when its name is unknown; `C:/…` globs are inside the prefix's `drive_c` |
| `launch.extra` | further programs in a suite (e.g. CorelDRAW → PHOTO-PAINT) |
| `platforms.macos.app` / `app_find` / `file` | native Mac build: the .app, a glob for it, the download file |
| `platforms.linux.exe_find` / `file` / `prefix` / `bridge` / `env` | native Linux build or Wine details; `bridge = "yabridge"` for audio plugins |
| `redeem.expires` (ISO date) / `expiry_text` | redemption deadline, parsed and as stated |
| `redeem.tier_products` / `products` | per-bundle / per-tier pages a coupon is applied on |
| `[download]` `scriptable`, `how`, `api`, `auth`, `endpoints` | whether the redeemed files can be fetched by script, and how |
| `[library]` `files`, `videos`, `ebooks`, … | what lands in the library folder after redemption; the tool's layout is `<library>/<vendor>/<machine_name>/` for vendor and item routes and `<library>/<ebook\|audio\|files>/<machine_name>/` for generic ones — describe contents, don't prescribe other paths |
| `service = true` | a code for an online service: nothing to download |
| `skip` / `skip_reason` | product deliberately not installed (e.g. Windows system utilities under Wine) |

Matching precedence, highest first: Steam keys always go to `generic/steam-key`;
then item `machine_names`; then `before_vendors` generic routes (Humble's own
coupons); then vendor rules, where a bundle's `redeem_text` beats its
`bundle_slugs`, which beat `developers` (where a key is redeemed is decided by
the bundle, not by who made the item); then generic routes by delivery.
`hi-recipes cover --conflicts` lists items more than one vendor claims.

Extracted keys resolve the same way, except that evidence on the key itself
outranks the bundle: Steam keys go to Steam; then item `machine_names`; then
any vendor whose `domains` appear in the key's instructions; then `key_names`;
then `bundle_slugs` (matched against the key's bundle). An item recipe's own
`[redeem]` is used before its vendor's.

## Rules for writing recipes

- Cite a source for every non-obvious claim (`[[sources]]`). If you inferred
  something, say so in `notes` and leave `verified = false`.
- Never guess silent-install switches or executable paths as fact; leave them
  out, or put them in `notes` as unconfirmed.
- Keys are never revealed or redeemed by default; recipes describe the steps,
  the tool asks before doing anything irreversible.
