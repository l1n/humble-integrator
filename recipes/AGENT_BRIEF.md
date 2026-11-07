# Brief for a vendor-recipe subagent

You are writing recipes for one vendor (or a small cluster of vendors) whose
Humble items can't go through the generic Steam-key or DRM-free routes.
The project is a generic tool, used by anyone with a Humble account, that gets
every Humble purchase into Steam on macOS and Linux.

Read first: `recipes/README.md` (the format), then the worked example
`recipes/vendors/profantasy.toml` and a few of `recipes/items/profantasy/*.toml`.

## Your inputs

- Your vendor's census rows: every bundle and item attributed to the vendor,
  with machine names, delivery icons, platforms and descriptions (given in
  your task as a JSONL path).
- The web. Prefer the vendor's own docs and support/forum posts, then Humble
  support articles, then community reports (Reddit, forums, WineHQ AppDB,
  ProtonDB, Lutris scripts).

## What to write (only inside your own paths)

- `recipes/vendors/<vendor>.toml` — match rules (developers/publishers as
  they appear in the census; `domains` and `key_names` for extracted keys),
  `[redeem]` (where and how a Humble key becomes the vendor's download or
  licence), `[install]` (installer technology, serial handling, unattended
  options), `[prefix]` and `[platforms.macos]` / `[platforms.linux]`.
- `recipes/items/<vendor>/<id>.toml` — one per *product*; list every Humble
  machine name for it from the census in `machine_names`. Kind, requires /
  recommends, install file, launch exe (apps), steam shortcut.
- If the vendor's installers have a wizard that needs a serial typed in, name
  the installer framework in `[install] driver` (e.g. `installaware`, `inno`,
  `nsis`, `msi`) and its silent switches if documented. The tool types the
  user's own serial; never propose bypassing activation or licence checks.

## Lessons from wave 1

- Start research at Humble's own help centre: it has a redemption article for
  almost every vendor, readable without a browser through its Zendesk API, e.g.
  `https://support.humblebundle.com/api/v2/help_center/articles/search.json?query=<vendor>`
  and `.../articles/<id>.json`. Cite the article URL. Web search is rationed
  across agents; prefer direct fetches of vendor help pages.
- Matching precedence (see recipes/README.md): Steam keys always go to Steam;
  then item machine names; then Humble coupons; then vendor rules, where
  `redeem_text` > `bundle_slugs` > `developers`. Use bundle-level rules for
  stores and marketplaces; use `developers` only for publishers that never sell
  through someone else's store.
- Don't give a vendor a `developers` rule if it also sells DRM-free Humble
  bundles that should just download (the generic files route handles those);
  items on a vendor route still get their Humble downloads fetched.
- Reuse the field names in recipes/README.md ("Fields that grew out of wave 1").
- Run `recipes.py cover --census data/census.jsonl --conflicts` as well as
  `cover`, and resolve any conflict involving your vendors.

## Rules

- Cite a source (`[[sources]]`) for every non-obvious claim. Mark inference
  in `notes`; keep `verified = false`.
- Don't guess executable names or switches as fact: use `launch.find` (a
  glob) when the exe name is unknown.
- Services (subscriptions, web apps, course platforms, codes for online
  accounts) are `kind = "service"`: redemption steps only, no Steam entry.
- Native macOS/Linux builds beat Windows-under-Wine; say so per platform.
- When done, run `python3 -I humble_integrator/recipes.py check` — it must
  report 0 problems — and finish with a short report: products covered,
  machine names covered, open questions, and anything you couldn't find.
