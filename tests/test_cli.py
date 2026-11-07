import io
import platform
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import cli  # noqa: E402
import humble  # noqa: E402
import recipes  # noqa: E402

PLAT = {"Darwin": "mac", "Linux": "linux"}.get(platform.system(), "linux")

ORDERS = [{
    "gamekey": "GK1",
    "product": {"machine_name": "mapmaking_megabundle_encore", "human_name": "Map Making MEGA Bundle Encore"},
    "subproducts": [
        {"machine_name": "coolgame", "human_name": "Cool Game", "downloads": [
            {"platform": PLAT, "download_struct": [{"name": "Download", "url": {"web": "https://dl/coolgame.zip"}}]}]},
        {"machine_name": "abook", "human_name": "A Book", "downloads": [
            {"platform": "ebook", "download_struct": [{"name": "EPUB", "url": {"web": "https://dl/abook.epub"}}]}]},
        {"machine_name": "dungeondesigner3", "human_name": "Dungeon Designer 3", "downloads": []},
    ],
    "tpkd_dict": {"all_tpks": [
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "somegame_steam", "human_name": "Some Game",
         "key_type": "steam", "steam_app_id": 1, "redeemed_key_val": "AAAAA-BBBBB-CCCCC"},
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "dungeondesigner3", "human_name": "Dungeon Designer 3",
         "key_type": "generic"},
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "mystery_key", "human_name": "Mystery Thing",
         "key_type": "generic"},
    ]},
}]


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.book = recipes.load()
        self.keys = [k for o in ORDERS for k in humble.keys_in_order(o)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_routes_every_owned_thing(self):
        plan = cli.make_plan(self.book, ORDERS, self.keys)
        self.assertEqual(len(plan["generic/steam-key"]), 1)
        self.assertEqual(len(plan["generic/drm-free-app"]), 1)
        self.assertEqual(len(plan["generic/ebook"]), 1)
        self.assertEqual(plan["profantasy/dd3"][0]["key"].human_name, "Dungeon Designer 3")
        self.assertEqual(plan["(uncovered)"][0]["key"].human_name, "Mystery Thing")
        out = io.StringIO()
        cli.print_plan(self.book, plan, out)
        self.assertIn("no recipe yet", out.getvalue())

    def test_steam_key_page_links_prefilled_activation(self):
        plan = cli.make_plan(self.book, ORDERS, self.keys)
        page = cli.steam_key_page(plan["generic/steam-key"], self.d / "keys.html").read_text()
        self.assertIn("registerkey?key=AAAAA-BBBBB-CCCCC", page)

    def test_native_build_is_unpacked_and_launcher_found(self):
        z = self.d / "coolgame.zip"
        with zipfile.ZipFile(z, "w") as zf:
            if PLAT == "mac":
                zf.writestr("Cool Game.app/Contents/MacOS/cool", "x")
            else:
                info = zipfile.ZipInfo("Cool Game/CoolGame.x86_64")
                info.external_attr = 0o755 << 16
                zf.writestr(info, b"\x7fELF" + b"\x00" * 60)
        real_fetch = humble.fetch
        humble.fetch = lambda d, dest, opener=None: z
        try:
            cfg = {"games": self.d / "games", "library": self.d / "lib", "umu": "umu-run"}
            plan = cli.make_plan(self.book, ORDERS, self.keys)
            name, exe = cli.install_build(cfg, plan["generic/drm-free-app"][0], log=lambda *_: None)
        finally:
            humble.fetch = real_fetch
        self.assertEqual(name, "Cool Game")
        self.assertIn("Cool", exe.name)

    def test_asset_pack_with_nothing_to_launch_goes_to_the_library(self):
        z = self.d / "coolgame.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Loops/kick_01.wav", "RIFF")
        real_fetch = humble.fetch
        humble.fetch = lambda d, dest, opener=None: z
        try:
            cfg = {"games": self.d / "games", "library": self.d / "lib", "umu": "umu-run"}
            plan = cli.make_plan(self.book, ORDERS, self.keys)
            got = cli.install_build(cfg, plan["generic/drm-free-app"][0], log=lambda *_: None)
        finally:
            humble.fetch = real_fetch
        self.assertIsNone(got)
        self.assertTrue((self.d / "lib/files/coolgame/Loops/kick_01.wav").exists())
        self.assertFalse((self.d / "games/coolgame/app").exists())

    def test_non_games_get_one_steam_entry_per_bundle(self):
        import steam
        root = self.d / "Steam"
        (root / "userdata" / "123" / "config").mkdir(parents=True)
        saved = (humble.fetch, steam.steam_roots, steam.steam_running)

        def fake_fetch(d, dest, opener=None):
            Path(dest).mkdir(parents=True, exist_ok=True)
            (Path(dest) / d.filename).write_text("x")
            return Path(dest) / d.filename

        humble.fetch, steam.steam_roots, steam.steam_running = fake_fetch, lambda: [root], lambda: False
        try:
            cfg = {"games": self.d / "g", "library": self.d / "lib", "umu": "umu-run"}
            plan = cli.make_plan(self.book, ORDERS, self.keys)
            cli.apply(cfg, self.book, plan, only={"generic/ebook", "profantasy/dd3"}, log=lambda *_: None)
        finally:
            humble.fetch, steam.steam_roots, steam.steam_running = saved
        page = self.d / "lib/bundles/map-making-mega-bundle-encore/index.html"
        text = page.read_text()
        self.assertIn("A Book", text)                      # downloaded ebook
        self.assertIn("Dungeon Designer 3", text)          # vendor key still to redeem
        self.assertIn("service.profantasy.com", text)
        entries = list(steam.read_shortcuts(root / "userdata/123/config").values())
        self.assertEqual([e["AppName"] for e in entries], ["Map Making MEGA Bundle Encore"])
        self.assertIn(str(page), entries[0]["LaunchOptions"])

    def test_bundle_filter_keeps_only_matching_orders_and_their_keys(self):
        other = {"gamekey": "GK2", "product": {"machine_name": "hib7", "human_name": "Humble Indie Bundle 7"}}
        got = cli.select_bundles(ORDERS + [other], ["indie bundle 7"])
        self.assertEqual([o["gamekey"] for o in got], ["GK2"])
        self.assertEqual(cli.select_bundles(ORDERS + [other], ["MAPMAKING"]), ORDERS)

    def test_bundle_entry_gets_humble_cover_art_and_an_icon_path(self):
        import steam
        root = self.d / "Steam"
        (root / "userdata" / "123" / "config").mkdir(parents=True)
        orders = [dict(ORDERS[0], subproducts=[dict(s, icon="https://img/" + s["machine_name"])
                                                 for s in ORDERS[0]["subproducts"]])]
        saved = (humble.fetch, steam.steam_roots, steam.steam_running, steam.art_from_images)
        seen = []

        def fake_fetch(d, dest, opener=None):
            Path(dest).mkdir(parents=True, exist_ok=True)
            (Path(dest) / d.filename).write_text("x")
            return Path(dest) / d.filename

        def fake_art(urls, opener=None):
            seen.append(urls)
            return {"cover": (b"C", ".jpg"), "icon": (b"I", ".png")}

        humble.fetch, steam.steam_roots, steam.steam_running = fake_fetch, lambda: [root], lambda: False
        steam.art_from_images = fake_art
        try:
            plan = cli.make_plan(self.book, orders, self.keys)
            cli.apply({"games": self.d / "g", "library": self.d / "lib", "umu": "umu-run"}, self.book, plan,
                      only={"generic/ebook"}, log=lambda *_: None)
        finally:
            humble.fetch, steam.steam_roots, steam.steam_running, steam.art_from_images = saved
        self.assertEqual(seen, [["https://img/abook"]])
        [entry] = steam.read_shortcuts(root / "userdata/123/config").values()
        self.assertTrue(entry["icon"].endswith("_icon.png"))
        self.assertEqual(Path(entry["icon"]).read_bytes(), b"I")
        self.assertIn("https://img/abook", (self.d / "lib/bundles/map-making-mega-bundle-encore/index.html").read_text())

    def test_one_failed_download_does_not_stop_the_rest(self):
        orders = [dict(ORDERS[0], subproducts=ORDERS[0]["subproducts"] + [
            {"machine_name": "bbook", "human_name": "B Book", "downloads": [
                {"platform": "ebook", "download_struct": [{"name": "EPUB", "url": {"web": "https://dl/bbook.epub"}}]}]}])]
        saved = humble.fetch

        def flaky_fetch(d, dest, opener=None):
            if d.filename == "abook.epub":
                raise RuntimeError("download cut short")
            Path(dest).mkdir(parents=True, exist_ok=True)
            (Path(dest) / d.filename).write_text("x")

        humble.fetch = flaky_fetch
        lines = []
        try:
            failed = cli.apply({"games": self.d / "g", "library": self.d / "lib", "umu": "umu-run",
                                "bundle_shortcuts": False}, self.book, cli.make_plan(self.book, orders, []),
                               only={"generic/ebook"}, log=lines.append)
        finally:
            humble.fetch = saved
        self.assertEqual(failed, ["abook.epub"])
        self.assertTrue((self.d / "lib/ebook/bbook/bbook.epub").exists())
        self.assertIn("1 failed", lines[-1])

    def test_dry_run_touches_nothing(self):
        cfg = {"games": self.d / "games", "library": self.d / "lib", "umu": "umu-run"}
        lines = []
        cli.apply(cfg, self.book, cli.make_plan(self.book, ORDERS, self.keys), dry_run=True, log=lines.append)
        self.assertFalse((self.d / "lib").exists() or (self.d / "games").exists())
        self.assertTrue(any("service.profantasy.com" in l for l in lines))

    def test_downloads_on_an_app_item_recipe_are_installed(self):
        root = self.d / "recipes"
        for rel, text in {
            "generic/files.toml": 'schema = 1\nname = "Files"\n[match]\ndelivery = ["hb-drmfree"]\n',
            "vendors/acme.toml": 'schema = 1\nname = "Acme"\n[redeem]\nwhere = "https://acme.example"\n',
            "items/acme/cool.toml": 'schema = 1\nname = "Cool"\nvendor = "acme"\nkind = "app"\n'
                                    'machine_names = ["coolgame"]\n[steam]\nshortcut = true\ntitle = "Cool Game Deluxe"\n',
        }.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        book = recipes.load(root)
        plan = cli.make_plan(book, ORDERS, [])
        self.assertIn("acme/cool", plan)
        lines = []
        cli.apply({"games": self.d / "g", "library": self.d / "l", "umu": "umu-run"}, book, plan,
                  only={"acme/cool"}, dry_run=True, log=lines.append)
        self.assertEqual(lines, ["app Cool Game (acme/cool)"])


if __name__ == "__main__":
    unittest.main()
