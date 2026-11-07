import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import census  # noqa: E402


class CandidatesTest(unittest.TestCase):
    def test_every_length_yields_valid_unique_candidates(self):
        for n in range(0, 60):
            ts = [f"{i:014d}" for i in range(n)]
            got = census.candidates(ts)
            self.assertLessEqual(len(got), census.MAX_CANDIDATES)
            self.assertEqual(len(got), len(set(got)), n)
            self.assertTrue(set(got) <= set(ts))
            if n:
                self.assertEqual(got[0], ts[0])
            if n > census.MAX_CANDIDATES:
                self.assertEqual(got[-1], ts[-1])


class ParseTest(unittest.TestCase):
    PAGE = ('<script id="webpack-bundle-page-data" type="application/json">' + json.dumps({"bundleData": {
        "machine_name": "b1", "basic_data": {"human_name": "B1", "end_time|datetime": "2025-12-02T19:00:00",
                                             "description": "A single key will be provided to redeem content"},
        "tier_order": ["t1"], "tier_display_data": {"t1": {"tier_item_machine_names": ["a"]}},
        "charity_data": {"charity_items": {"charity1": {}}},
        "tier_item_data": {
            "a": {"human_name": "A", "item_content_type": "software", "developers": [{"developer-name": "Dev"}],
                  "availability_icons": {"delivery_icons": ["hb-key"], "platform_icons": ["hb-windows"],
                                         "delivery_to_platform": {"hb-key": {"available": ["hb-windows"]}}}},
            "charity1": {"human_name": "Charity"},
        }}}) + "</script>")

    LEGACY = ('<script id="webpack-bundle-data" type="application/json">' + json.dumps({"bundleVars": {
        "product_machine_name": "old_softwarebundle", "product_human_name": "Old Bundle",
        "slideout_data": {"display_items": {
            "app1": {"machine_name": "app1", "human_name": "App One", "availability_icons": {"delivery_icons": ["hb-key"]},
                     "developers": [{"developer_name": "Old Dev"}], "publishers": []},
            "app2": {"machine_name": "app2", "human_name": "App Two", "availability_icons": {"delivery_icons": ["hb-key"]},
                     "developers": [], "publishers": [{"publisher_name": "Old Pub"}]}}}}}) + '</script>'
        '<h2 class="dd-header-headline"> Pay $1 or more to unlock! </h2><div data-slideout="app1"></div>'
        '<p>Redeem for Windows Mac not available Locked content MSRP: $9.99</p>'
        '<h2 class="dd-header-headline"> Pay $15 or more! </h2><div data-slideout="app2"></div>'
        '<p>Redeem your keys. All keys are redeemable on the Example website.</p>')

    def test_legacy_2019_pages_are_normalized(self):
        rec = census.bundle_record("software", "old", "2019", census.extract_bundle_data(self.LEGACY))
        self.assertEqual((rec["human_name"], rec["machine_name"]), ("Old Bundle", "old_softwarebundle"))
        items = {i["machine_name"]: i for i in rec["items"]}
        self.assertEqual(items["app1"]["developers"], ["Old Dev"])
        self.assertEqual(items["app2"]["publishers"], ["Old Pub"])
        self.assertEqual((items["app1"]["tiers"], items["app2"]["tiers"]), (["tier0"], ["tier1"]))
        self.assertIn("redeemable on the Example website", rec["redeem_text"])
        self.assertNotIn("MSRP", rec["redeem_text"])

    def test_has_any_keys_false_means_drm_free(self):
        bundle = census.extract_bundle_data(self.LEGACY)
        rec = census.bundle_record("software", "old", "2019", bundle, self.LEGACY + '"has_any_keys": false')
        self.assertEqual((rec["has_keys"], rec["drm_free"]), (False, True))
        rec = census.bundle_record("software", "old", "2019", bundle, '"has_any_keys": true')
        self.assertEqual((rec["has_keys"], rec["drm_free"]), (True, False))

    def test_parse_merges_cache_into_existing_census(self):
        import gzip
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            page = d / "cache" / "pages" / "software" / "b1.html.gz"
            page.parent.mkdir(parents=True)
            page.write_bytes(gzip.compress(self.PAGE.encode()))
            page.with_name("b1.ts").write_text("2025")
            out = d / "census.jsonl"
            out.write_text(json.dumps({"category": "games", "slug": "old", "items": []}) + "\n")
            census.main(["parse", "--cache", str(d / "cache"), "--out", str(out)])
            slugs = [json.loads(l)["slug"] for l in out.read_text().splitlines()]
            self.assertEqual(slugs, ["old", "b1"])

    def test_extracts_items_tiers_and_skips_charity(self):
        rec = census.bundle_record("software", "b1", "2025", census.extract_bundle_data(self.PAGE))
        self.assertEqual([i["machine_name"] for i in rec["items"]], ["a"])
        item = rec["items"][0]
        self.assertEqual((item["tiers"], item["delivery"], item["developers"]), (["t1"], ["hb-key"], ["Dev"]))
        self.assertEqual(item["delivery_to_platform"], {"hb-key": ["hb-windows"]})
        self.assertTrue(rec["single_key"])


if __name__ == "__main__":
    unittest.main()
