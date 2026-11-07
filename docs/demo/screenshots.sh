#!/usr/bin/env bash
# Regenerate docs/img/* from a fake demo account (no real keys, no real account).
# Needs: nix (for charm-freeze and oxipng), Firefox (headless screenshot of the key page).
set -euo pipefail
cd "$(dirname "$0")/../.."
demo=$(mktemp -d)
trap 'rm -rf "$demo"' EXIT
python3 -I docs/demo/seed_demo_account.py "$demo" >/dev/null

shot() {  # shot OUT.png "shown command" real command...
  local out=$1 shown=$2; shift 2
  nix shell nixpkgs#charm-freeze -c freeze \
    --execute "sh -c 'echo \"\$ $shown\"; echo; $*'" \
    --window --theme dracula --padding 20 --font.size 14 --border.radius 8 --shadow.blur 10 --wrap 112 \
    -o "docs/img/$out" >/dev/null
}

shot hi-plan.png "hi plan" "env HOME=$demo python3 humble_integrator/cli.py plan"
shot hi-apply-dry-run.png "hi apply --dry-run" \
  "env HOME=$demo python3 humble_integrator/cli.py apply --dry-run --only generic/steam-key generic/drm-free-app \
   generic/ebook generic/audio generic/humble-coupon profantasy/cc3plus profantasy/dd3 profantasy/annual-vol17"
shot hi-recipes-cover.png "hi-recipes cover --census data/census --top 15" \
  "python3 humble_integrator/recipes.py cover --census data/census --top 15 | sed -n 1,16p"

HOME=$demo python3 -I - "$demo/steam-keys.html" <<'EOF'
import sys
sys.path.insert(0, "humble_integrator")
import cli, humble, recipes
plan = cli.make_plan(recipes.load(), humble.cached_orders(), list(humble.KeyStore().keys.values()))
cli.steam_key_page(plan["generic/steam-key"], sys.argv[1])
EOF
profile=$(mktemp -d)
/Applications/Firefox.app/Contents/MacOS/firefox --headless --no-remote --profile "$profile" \
  --window-size=760,330 --screenshot "$PWD/docs/img/steam-keys-page.png" "file://$demo/steam-keys.html" 2>/dev/null
rm -rf "$profile"

chmod 644 docs/img/*.png
nix shell nixpkgs#oxipng -c oxipng -q -o 4 --strip safe docs/img/*.png
ls -la docs/img
