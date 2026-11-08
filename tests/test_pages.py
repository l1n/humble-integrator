import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import pages  # noqa: E402

# A bundle page as Humble has served it since about 2020: the bundle in a JSON script.
MODERN = (
    '<html><head><title>Humble Tech Book Bundle: Hacking (pay what you want and help charity)</title>'
    '<meta property="og:image" content="https://hb.imgix.net/share.png?w=1200&amp;s=1"></head>'
    '<script id="webpack-bundle-page-data" type="application/json">' + json.dumps({"bundleData": {
        "machine_name": "hacking_bookbundle", "basic_data": {"human_name": "Hacking"},
        "high_res_tile_image": "https://hb.imgix.net/tile.png?w=1200&s=2",
        "tile_logo": "https://hb.imgix.net/logo.png?h=170&s=3",
        "tier_item_data": {
            "malware": {"human_name": "Practical Malware Analysis",
                        "resolved_paths": {"front_page_art_imgix_retina": "https://hb.imgix.net/m.png?s=4"}},
            "bitcoin": {"human_name": "Bitcoin for the Befuddled", "preview_image": "https://hb.imgix.net/b.jpg?s=5"},
        }}}) + '</script></html>')

# About 2017-2020: the items in the page's script data.
SCRIPTED = ('<title>Humble Book Bundle: Data Science presented by O&#39;Reilly</title>'
            "<meta name='og:image' content='https://humblebundle.imgix.net/share.jpg'>"
            '<script>var bundleVars = {"product_machine_name": "datascience_bookbundle", "slideout_data": '
            '{"display_items": {"thinkstats": {"human_name": "Think Stats", "preview_image": '
            '"https://humblebundle.imgix.net/ts.jpg?auto\\u003dformat"}, "x": {"human_name": "No Picture"}}}};</script>')

# About 2013-2016: a pane per item, its picture (sometimes loaded lazily), then its name.
PANES = ("<title>Humble Comics Bundle: My Little Pony (pay what you want)</title>"
         "<style>.game-info-pane img { width: 540px }</style>"
         "<div id='game-info-a' class='game-info-pane js-game-info-pane'><div class='game-video-or-image'>"
         '<img src="data:image/gif;base64,R0lG" data-src="https://cdn.example/a.jpg" class="lazy-load-bundle-img"/>'
         "</div><div class='game-description'><h2>My Little Pony: Friends Forever #1-5</h2></div></div>"
         "<div id='game-info-b' class='game-info-pane'><img src=\"https://cdn.example/b.jpg\" alt=\"\"/>"
         "<h2>Fiendship is Magic &amp; More</h2></div>"
         "<div id='game-info-c' class='game-info-pane'><iframe src='https://youtube.com/x'></iframe><h2>A Video</h2></div>")


class PageTest(unittest.TestCase):
    def test_titles_match_whole_words_only(self):
        self.assertTrue(pages.titled("Humble Indie Bundle 7 (pay what you want)", "Humble Indie Bundle 7"))
        self.assertTrue(pages.titled("Humble Book Bundle: Data Science presented by O&#39;Reilly",
                                     "Humble Book Bundle: Data Science presented by O'Reilly"))
        self.assertFalse(pages.titled("Humble Indie Bundle 10 (pay what you want)", "Humble Indie Bundle 1"))
        self.assertFalse(pages.titled("Humble Weekly Bundle", "Humble Indie Bundle 7"))

    def test_a_page_is_the_bundles_by_its_machine_name_or_title(self):
        self.assertTrue(pages.is_page_of(MODERN, {"machine_name": "hacking_bookbundle", "human_name": "?"}))
        self.assertFalse(pages.is_page_of(MODERN, {"machine_name": "hacking", "human_name": "Other"}))
        self.assertFalse(pages.is_page_of('"x_hacking_bookbundle"', {"machine_name": "hacking_bookbundle"}))
        self.assertTrue(pages.is_page_of(PANES, {"machine_name": "mlp_bookbundle",
                                                 "human_name": "Humble Comics Bundle: My Little Pony"}))

    def test_art_and_item_pictures_from_every_kind_of_page(self):
        art = pages.page_art(MODERN)
        self.assertEqual((art["hero"], art["wide"], art["logo"]), ("https://hb.imgix.net/tile.png?w=1200&s=2",
                                                                    "https://hb.imgix.net/share.png?w=1200&s=1",
                                                                    "https://hb.imgix.net/logo.png?h=170&s=3"))
        self.assertEqual(art["items"], [["Practical Malware Analysis", "https://hb.imgix.net/m.png?s=4"],
                                        ["Bitcoin for the Befuddled", "https://hb.imgix.net/b.jpg?s=5"]])
        art = pages.page_art(SCRIPTED)
        self.assertEqual((art["hero"], art["wide"], art["logo"]),
                         ("https://humblebundle.imgix.net/share.jpg", "https://humblebundle.imgix.net/share.jpg", ""))
        self.assertEqual(art["items"], [["Think Stats", "https://humblebundle.imgix.net/ts.jpg?auto=format"]])
        self.assertEqual(pages.page_art(PANES)["items"], [["My Little Pony: Friends Forever #1-5", "https://cdn.example/a.jpg"],
                                                          ["Fiendship is Magic & More", "https://cdn.example/b.jpg"]])

    def test_find_tries_the_bundles_own_page_then_the_landing_page_of_the_day_it_was_bought(self):
        asked = []

        def live(url):
            asked.append(url)
            return None  # the bundle's page is gone

        def archive(url):
            asked.append(url)
            if "wayback/available" in url:
                return json.dumps({"archived_snapshots": {"closest": {"available": True, "timestamp": "20141202034756"}}}).encode()
            return PANES.encode() if "20141202034756id_/https://www.humblebundle.com/books" in url else None

        bundle = {"machine_name": "mylittlepony_bookbundle", "human_name": "Humble Comics Bundle: My Little Pony",
                  "bought": "2014-12-02T10:00:00"}
        index = {"mylittlepony_bookbundle": ("books", "mlp-books", "20141201000000")}
        with tempfile.TemporaryDirectory() as d:
            found = pages.find(bundle, cache_dir=d, pages=index, live=live, archive=archive)
            self.assertEqual(asked, ["https://www.humblebundle.com/books/mlp-books",
                                     "https://web.archive.org/web/20141201000000id_/https://www.humblebundle.com/books/mlp-books",
                                     "https://archive.org/wayback/available?url=https%3A//www.humblebundle.com/books&timestamp=20141202",
                                     "https://web.archive.org/web/20141202034756id_/https://www.humblebundle.com/books"])
            self.assertEqual(found["page"], asked[-1])
            self.assertEqual(len(found["items"]), 2)
            asked.clear()
            self.assertEqual(pages.find(bundle, cache_dir=d, pages=index, live=live, archive=archive), found)
            self.assertEqual(asked, [])  # looked up once
            self.assertEqual(pages.find({"machine_name": "nope", "human_name": "Nope"}, cache_dir=d, pages={},
                                        live=live, archive=archive), {})  # nothing to go on: no page

            def down(url):
                raise RuntimeError("giving up")
            with self.assertRaises(RuntimeError):
                pages.find(dict(bundle, machine_name="other_bookbundle"), cache_dir=d, pages={}, live=live, archive=down)
            self.assertFalse((Path(d) / "other_bookbundle.json").exists())  # a network failure isn't kept

    def test_pictures_gone_from_humble_come_from_the_archive(self):
        jpeg = b"\xff\xd8\xff\xe0rest"
        saved = (pages._live_get, pages._archive_get)
        pages._live_get = lambda url: None
        pages._archive_get = lambda url: jpeg if url == "https://web.archive.org/web/2012im_/https://x/a.jpg" else None
        try:
            self.assertEqual(pages.picture("https://x/a.jpg", "https://web.archive.org/web/2012id_/https://x/"),
                             (jpeg, ".jpg"))
            self.assertIsNone(pages.picture("https://x/a.jpg", "https://www.humblebundle.com/books/x"))
        finally:
            pages._live_get, pages._archive_get = saved


if __name__ == "__main__":
    unittest.main()
