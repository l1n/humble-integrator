import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import recipes  # noqa: E402

REPO_RECIPES = Path(__file__).resolve().parent.parent / "recipes"


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class RecipeBookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, "generic/steam-key.toml",
              'schema = 1\nname = "Steam key"\npriority = 10\n[match]\ndelivery = ["hb-steam"]\n')
        write(self.root, "vendors/acme.toml",
              'schema = 1\nname = "Acme"\n[match]\ndevelopers = ["Acme Software"]\n'
              '[platforms.linux]\nrunner = "proton"\nwindows = "win10"\n')
        write(self.root, "items/acme/app.toml",
              'schema = 1\nname = "Acme App"\nvendor = "acme"\nkind = "app"\nmachine_names = ["acmeapp"]\n'
              '[launch]\nexe = "acme.exe"\n[steam]\nshortcut = true\n'
              '[platforms.linux]\nwindows = "winxp64"\n')
        write(self.root, "items/acme/addon.toml",
              'schema = 1\nname = "Acme Addon"\nvendor = "acme"\nkind = "addon"\n'
              'machine_names = ["acmeaddon", "acmeaddon_bundle2"]\nrequires = ["acme/app"]\n'
              '[install]\nthen = ["run:acme/app"]\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolution_prefers_item_then_vendor_then_generic(self):
        book = recipes.load(self.root)
        item = lambda mn, dev="", delivery=(): {
            "machine_name": mn, "developers": [dev], "publishers": [], "delivery": list(delivery)}
        self.assertEqual(book.resolve(item("acmeaddon_bundle2"))[0].id, "acme/addon")
        self.assertEqual(book.resolve(item("acme_unknown", "Acme Software"))[0].id, "vendors/acme")
        self.assertEqual(book.resolve(item("somegame", "Other", ["hb-steam"]))[0].id, "generic/steam-key")
        self.assertEqual(book.resolve(item("mystery", "Other", ["hb-key"])), (None, None))

    def test_vendor_claims_items_by_bundle_slug_or_redeem_text(self):
        write(self.root, "vendors/market.toml",
              'schema = 1\nname = "Market"\n[match]\nbundle_slugs = ["^market-"]\n'
              'redeem_text = ["GameDev ?Market"]\n')
        book = recipes.load(self.root)
        item = {"machine_name": "tiles", "developers": ["SomeSeller"], "publishers": [], "delivery": ["hb-key"]}
        self.assertEqual(book.resolve(item, {"slug": "market-pack"})[0].id, "vendors/market")
        self.assertEqual(book.resolve(item, {"slug": "x", "redeem_text": "Keys on the GamedevMarket website"})[0].id,
                         "vendors/market")
        self.assertEqual(book.resolve(item, {"slug": "x", "redeem_text": "Redeem on Steam"}), (None, None))

    def test_humble_coupons_outrank_bundle_wide_vendor_rules(self):
        write(self.root, "vendors/market.toml",
              'schema = 1\nname = "Market"\n[match]\nredeem_text = ["Market"]\n')
        write(self.root, "generic/humble-coupon.toml",
              'schema = 1\nname = "Coupon"\nbefore_vendors = true\n[match]\n'
              'machine_names = ["^humblemonthly_"]\ndevelopers = ["Humble Choice"]\n')
        book = recipes.load(self.root)
        b = {"slug": "x", "redeem_text": "Keys on the Market website"}
        coupon = {"machine_name": "humblemonthly_10percentoff_general", "developers": [], "publishers": [],
                  "delivery": ["hb-key"]}
        asset = {"machine_name": "tiles", "developers": ["Seller"], "publishers": [], "delivery": ["hb-key"]}
        self.assertEqual(book.resolve(coupon, b)[0].id, "generic/humble-coupon")
        self.assertEqual(book.resolve(asset, b)[0].id, "vendors/market")
        self.assertEqual(book.resolve(dict(asset, developers=["Humble Choice"]), b)[0].id, "generic/humble-coupon")
        self.assertEqual(book.resolve_key({"machine_name": "humblemonthly_10percentoff_general", "human_name": "",
                                           "key_type": "generic", "bundle": "Market bundle"})[0].id,
                         "generic/humble-coupon")

    def test_bundle_rules_beat_developer_rules_and_steam_keys_go_to_steam(self):
        write(self.root, "vendors/aaa-publisher.toml",
              'schema = 1\nname = "Publisher"\n[match]\ndevelopers = ["Synty Studios"]\n')
        write(self.root, "vendors/zzz-store.toml",
              'schema = 1\nname = "Store"\n[match]\nredeem_text = ["Unity Asset Store"]\n')
        book = recipes.load(self.root)
        b = {"slug": "x", "redeem_text": "Redeemable on Unity Asset Store website"}
        asset = {"machine_name": "polygon_pack", "developers": ["Synty Studios"], "publishers": [],
                 "delivery": ["hb-key"]}
        self.assertEqual(book.resolve(asset, b)[0].id, "vendors/zzz-store")
        self.assertEqual(book.resolve(asset, {"slug": "y"})[0].id, "vendors/aaa-publisher")
        steam_app = {"machine_name": "acmeapp", "developers": [], "publishers": [], "delivery": ["hb-steam"]}
        self.assertEqual(book.resolve(steam_app, b)[0].id, "generic/steam-key")
        self.assertEqual(book.resolve_key({"machine_name": "acmeapp", "key_type": "steam", "steam_app_id": 9})[0].id,
                         "generic/steam-key")

    def test_two_maker_bundle_ties_go_to_each_items_maker(self):
        write(self.root, "vendors/aaa-animpic.toml",
              'schema = 1\nname = "Animpic"\n[match]\nredeem_text = ["animpic"]\n')
        write(self.root, "vendors/zzz-eldamar.toml",
              'schema = 1\nname = "Eldamar"\n[match]\nredeem_text = ["eldamar"]\ndevelopers = ["Eldamar Studio"]\n')
        book = recipes.load(self.root)
        b = {"slug": "low-poly", "redeem_text": "redeemable through Animpic and Eldamar websites"}
        eld = {"machine_name": "e", "developers": ["Eldamar Studio"], "publishers": [], "delivery": ["hb-key"]}
        ani = {"machine_name": "a", "developers": ["Animpic Studio"], "publishers": [], "delivery": ["hb-key"]}
        self.assertEqual(book.resolve(eld, b)[0].id, "vendors/zzz-eldamar")
        self.assertEqual(book.resolve(ani, b)[0].id, "vendors/aaa-animpic")
        self.assertFalse(book.claims_tied(eld, b))
        self.assertTrue(book.claims_tied(dict(ani, developers=["Someone"]), b))

    def test_key_evidence_beats_an_earlier_vendors_bundle_slug(self):
        write(self.root, "vendors/aaa-fab.toml",
              'schema = 1\nname = "Fab"\n[match]\nbundle_slugs = ["battle-hardened"]\n')
        write(self.root, "vendors/zzz-unity.toml",
              'schema = 1\nname = "Unity"\n[match]\ndomains = ["assetstore.unity.com"]\n'
              'key_names = ["[(]unity[)]"]\n')
        book = recipes.load(self.root)
        base = {"machine_name": "kit", "key_type": "generic", "bundle": "Battle Hardened",
                "bundle_machine_name": "battle_hardened", "instructions": ""}
        self.assertEqual(book.resolve_key(dict(base, human_name="Castle Kit (Unity)"))[0].id, "vendors/zzz-unity")
        self.assertEqual(book.resolve_key(dict(base, human_name="Castle Kit",
                                               instructions="redeem at assetstore.unity.com"))[0].id,
                         "vendors/zzz-unity")
        self.assertEqual(book.resolve_key(dict(base, human_name="Castle Kit (Unreal)"))[0].id, "vendors/aaa-fab")

    def test_pack_parts_without_delivery_follow_the_bundles_drm_free_flag(self):
        write(self.root, "generic/files.toml",
              'schema = 1\nname = "Files"\npriority = 90\n[match]\ndelivery = ["hb-drmfree"]\n')
        book = recipes.load(self.root)
        part = {"machine_name": "mini", "developers": [], "publishers": [], "delivery": []}
        self.assertEqual(book.resolve(part, {"slug": "x", "drm_free": True})[0].id, "generic/files")
        self.assertEqual(book.resolve(part, {"slug": "x", "drm_free": False}), (None, None))

    def test_keys_resolve_by_machine_name_vendor_domain_then_key_type(self):
        write(self.root, "vendors/acme.toml",
              'schema = 1\nname = "Acme"\n[match]\ndevelopers = ["Acme Software"]\n'
              'domains = ["acme.example"]\nkey_names = ["acme.*bundle key"]\n')
        write(self.root, "generic/steam-key.toml",
              'schema = 1\nname = "Steam key"\n[match]\ndelivery = ["hb-steam"]\n'
              'key_types = ["steam"]\nsteam_app_id = true\n')
        book = recipes.load(self.root)
        key = lambda **kw: {"machine_name": "x", "human_name": "", "instructions": "",
                            "key_type": "generic", "steam_app_id": None, **kw}
        self.assertEqual(book.resolve_key(key(machine_name="acmeapp"))[0].id, "acme/app")
        self.assertEqual(book.resolve_key(key(instructions="Redeem at https://acme.example/r"))[0].id,
                         "vendors/acme")
        self.assertEqual(book.resolve_key(key(human_name="ACME Mega Bundle Key"))[0].id, "vendors/acme")
        self.assertEqual(book.resolve_key(key(steam_app_id=5))[0].id, "generic/steam-key")
        write(self.root, "vendors/courses.toml",
              'schema = 1\nname = "Courses"\n[match]\nbundle_slugs = ["courses-from-acme"]\n')
        book = recipes.load(self.root)
        self.assertEqual(book.resolve_key(key(human_name="Master DynamoDB", bundle="Courses from ACME!",
                                              bundle_machine_name="x"))[0].id, "vendors/courses")
        self.assertEqual(book.resolve_key(key()), (None, None))

    def test_check_passes_and_orders_dependencies_first(self):
        book = recipes.load(self.root)
        self.assertEqual(recipes.check(book), [])
        self.assertEqual(recipes.order(book, ["acme/addon"]), ["acme/app", "acme/addon"])

    def test_recommends_orders_but_does_not_pull_in(self):
        write(self.root, "items/acme/pack.toml",
              'schema = 1\nname = "Acme Pack"\nvendor = "acme"\nkind = "content"\n'
              'machine_names = ["acmepack"]\nrecommends = ["acme/app"]\n')
        book = recipes.load(self.root)
        self.assertEqual(recipes.order(book, ["acme/pack"]), ["acme/pack"])
        self.assertEqual(recipes.order(book, ["acme/pack", "acme/app"]), ["acme/app", "acme/pack"])

    def test_check_reports_bad_references_and_cycles(self):
        write(self.root, "items/acme/addon.toml",
              'schema = 1\nname = "Acme Addon"\nvendor = "acme"\nkind = "addon"\n'
              'machine_names = ["acmeaddon"]\nrequires = ["acme/missing"]\n')
        write(self.root, "items/acme/app.toml",
              'schema = 1\nname = "Acme App"\nvendor = "acme"\nkind = "app"\nmachine_names = ["acmeapp"]\n'
              'requires = ["acme/app2"]\n[steam]\nshortcut = true\n')
        write(self.root, "items/acme/app2.toml",
              'schema = 1\nname = "Acme App 2"\nvendor = "acme"\nkind = "app"\nmachine_names = ["acmeapp2"]\n'
              'requires = ["acme/app"]\n')
        problems = "\n".join(recipes.check(recipes.load(self.root)))
        self.assertIn("requires/recommends unknown recipe 'acme/missing'", problems)
        self.assertIn("steam.shortcut needs launch.exe or launch.find", problems)
        self.assertIn("dependency cycle", problems)

    def test_duplicate_machine_name_is_an_error(self):
        write(self.root, "items/acme/dupe.toml",
              'schema = 1\nname = "Dupe"\nvendor = "acme"\nkind = "app"\nmachine_names = ["acmeapp"]\n')
        with self.assertRaises(recipes.RecipeError):
            recipes.load(self.root)

    def test_platform_view_merges_item_over_vendor(self):
        book = recipes.load(self.root)
        self.assertEqual(recipes.platform_view(book, "acme/app", "linux"),
                         {"runner": "proton", "windows": "winxp64"})

    def test_coverage_counts_layers_and_uncovered(self):
        census = self.root / "census.jsonl"
        census.write_text(json.dumps({"category": "software", "slug": "b", "items": [
            {"machine_name": "acmeapp", "developers": [], "publishers": [], "delivery": ["hb-key"]},
            {"machine_name": "g", "developers": ["X"], "publishers": [], "delivery": ["hb-steam"]},
            {"machine_name": "m", "developers": ["Y"], "publishers": [], "delivery": ["hb-key"]},
        ]}) + "\n")
        total, by_layer, _, uncovered = recipes.coverage(recipes.load(self.root), census)
        self.assertEqual(total, 3)
        self.assertEqual(by_layer, {"item": 1, "generic": 1})
        self.assertEqual(list(uncovered), ["hb-key | Y"])


class ShippedRecipesTest(unittest.TestCase):
    def test_shipped_recipe_book_is_consistent(self):
        self.assertEqual(recipes.check(recipes.load(REPO_RECIPES)), [])

    def test_offers_physical_rewards_and_undelivered_books_route_by_name(self):
        book = recipes.load(REPO_RECIPES)
        books = {"slug": "x-books", "category": "books", "items": []}
        games = {"slug": "x", "category": "games", "items": []}
        item = lambda mn, **kw: dict({"machine_name": mn, "developers": [], "publishers": []}, **kw)
        route = lambda it, b=books: book.resolve(it, b)[0].id
        # 2017-2020 book pages record no delivery: the books were DRM-free downloads
        self.assertEqual(route(item("discountarmageddon")), "generic/ebook")          # a novel
        self.assertEqual(route(item("physicallybasedshaderdevelopmentforunity2017")), "generic/ebook")
        self.assertEqual(route(item("gateofbonesbook2", content_type="audiobook")), "generic/audio")
        self.assertEqual(route(item("exclusivediscountsoninformit")), "generic/partner-offer")
        self.assertEqual(route(item("dcuiultra30-daytrialoffer")), "generic/partner-offer")
        self.assertEqual(route(item("monstrositiesphysicalcopy")), "generic/physical")
        self.assertEqual(route(item("dune_corerulebook_physical")), "generic/physical")
        # a coupon for a Steam game shows Steam's icon but isn't a Steam key...
        self.assertEqual(route(item("couponfrostpunk235off", delivery=["hb-steam"]), games), "generic/humble-coupon")
        self.assertEqual(book.resolve_key({"machine_name": "couponfrostpunk235off", "key_type": "steam",
                                           "steam_app_id": 1})[0].id, "generic/humble-coupon")
        # ...while a vendor's own coupon keeps its recipe, which says where to redeem it
        self.assertEqual(route(item("unityassetstore10offcoupon", delivery=["hb-key"]), games),
                         "unity-asset-store/asset-store-10-off-coupon")
        self.assertEqual(book.resolve_key({"machine_name": "websitecoupon20_ebooksdotcom",
                                           "key_type": "generic"})[0].id, "generic/partner-offer")


if __name__ == "__main__":
    unittest.main()
