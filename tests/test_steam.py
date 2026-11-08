import binascii
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import steam  # noqa: E402


def raw_shortcuts_vdf(entries):
    """Hand-encode a shortcuts.vdf the way Steam writes it, independent of steam.bvdf_dump."""
    out = bytearray(b"\x00shortcuts\x00")
    for i, (appid, name, exe, tags) in enumerate(entries):
        out += b"\x00" + str(i).encode() + b"\x00"
        out += b"\x02appid\x00" + struct.pack("<i", appid)
        out += b"\x01AppName\x00" + name.encode() + b"\x00"
        out += b"\x01Exe\x00" + exe.encode() + b"\x00"
        out += b"\x00tags\x00"
        for j, t in enumerate(tags):
            out += b"\x01" + str(j).encode() + b"\x00" + t.encode() + b"\x00"
        out += b"\x08"
        out += b"\x08"
    out += b"\x08\x08"
    return bytes(out)


class SteamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_shortcut_id_matches_steams_formula(self):
        exe, name = '"/Applications/Foo.app"', "Foo"
        expected = (binascii.crc32((exe + name).encode()) & 0xFFFFFFFF) | 0x80000000
        self.assertEqual(steam.shortcut_id(exe, name), expected)
        self.assertTrue(steam.shortcut_id(exe, name) & 0x80000000)
        self.assertEqual(steam.game_id(1) & 0xFFFFFFFF, 0x02000000)

    def test_reads_a_file_written_by_steam(self):
        (self.cfg / "shortcuts.vdf").write_bytes(raw_shortcuts_vdf([(-5, "Mine", '"/x"', ["Fav"])]))
        s = steam.read_shortcuts(self.cfg)["0"]
        self.assertEqual((s["appid"], s["AppName"], s["tags"]), (-5, "Mine", {"0": "Fav"}))

    def test_binary_vdf_round_trips(self):
        sid, entry = steam.make_shortcut("Campaign Cartographer 3+", "/opt/cc3/run.sh")
        data = {"shortcuts": {"0": entry}}
        self.assertEqual(steam.bvdf_load(steam.bvdf_dump(data)), data)
        self.assertEqual(entry["appid"] & 0xFFFFFFFF, sid)

    def test_upsert_keeps_user_entries_and_replaces_ours(self):
        (self.cfg / "shortcuts.vdf").write_bytes(raw_shortcuts_vdf([(-5, "Mine", '"/x"', ["Fav"])]))
        sid, entry = steam.make_shortcut("CC3+", "/opt/cc3/run.sh")
        steam.upsert_shortcuts(self.cfg, [entry])
        entry2 = dict(entry, LaunchOptions="--foo")
        steam.upsert_shortcuts(self.cfg, [entry2])
        names = {s["AppName"]: s for s in steam.read_shortcuts(self.cfg).values()}
        self.assertEqual(set(names), {"Mine", "CC3+"})
        self.assertEqual(names["CC3+"]["LaunchOptions"], "--foo")
        self.assertTrue(list(self.cfg.glob("shortcuts.vdf.bak-*")), "backs up before writing")
        steam.upsert_shortcuts(self.cfg, [], remove_ids=[sid])
        self.assertEqual({s["AppName"] for s in steam.read_shortcuts(self.cfg).values()}, {"Mine"})

    def test_remove_never_touches_entries_without_our_tag(self):
        user_sid = steam.shortcut_id('"/x"', "Mine")
        (self.cfg / "shortcuts.vdf").write_bytes(
            raw_shortcuts_vdf([(steam._to_i32(user_sid), "Mine", '"/x"', [])]))
        steam.upsert_shortcuts(self.cfg, [], remove_ids=[user_sid])
        self.assertEqual(len(steam.read_shortcuts(self.cfg)), 1)

    def test_reinstalled_item_replaces_its_old_shortcut_but_not_the_users(self):
        user_sid = steam.shortcut_id('"/x"', "Cave Story+")
        (self.cfg / "shortcuts.vdf").write_bytes(
            raw_shortcuts_vdf([(steam._to_i32(user_sid), "Cave Story+", '"/x"', [])]))
        _, mac = steam.make_shortcut("Cave Story+", "/Games/cave/Cave Story+.app")
        steam.upsert_shortcuts(self.cfg, [mac])
        _, win = steam.make_shortcut("Cave Story+", "/Games/cave/launch.sh")    # now via Wine
        steam.upsert_shortcuts(self.cfg, [win])
        exes = sorted(e["Exe"] for e in steam.read_shortcuts(self.cfg).values())
        self.assertEqual(exes, ['"/Games/cave/launch.sh"', '"/x"'])

    def test_artwork_file_names(self):
        paths = steam.write_artwork(self.cfg, 3000000001, {"cover": (b"x", ".png"), "hero": (b"y", ".jpg")})
        self.assertEqual(sorted(p.name for p in paths), ["3000000001_hero.jpg", "3000000001p.png"])

    def test_compat_tool_mapping_preserves_existing_config(self):
        (self.cfg / "config").mkdir()
        (self.cfg / "config" / "config.vdf").write_text(
            '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
            '\t\t\t\t"AutoUpdateWindowEnabled"\t\t"0"\n\t\t\t}\n\t\t}\n\t}\n}\n')
        steam.set_compat_tool(self.cfg, 3000000001, "proton_9")
        data = steam.vdf_loads((self.cfg / "config" / "config.vdf").read_text())
        s = data["InstallConfigStore"]["Software"]["Valve"]["Steam"]
        self.assertEqual(s["AutoUpdateWindowEnabled"], "0")
        self.assertEqual(s["CompatToolMapping"]["3000000001"]["name"], "proton_9")

    def test_steamgriddb_art_picks_first_hit_per_kind(self):
        import io
        import json

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        auth = []

        class Opener:
            title = "Campaign Cartographer 3+"

            def open(self, req, timeout=None):
                url = req.full_url
                auth.append((url, req.headers.get("Authorization"), req.headers.get("User-agent")))
                if "autocomplete" in url:
                    return Resp(json.dumps({"data": [{"id": 42, "name": self.title}]}).encode())
                if "/grids/game/42?dimensions=600x900" in url:
                    return Resp(json.dumps({"data": [{"url": "https://cdn/x/cover.jpg"}]}).encode())
                if url.startswith("https://cdn/"):
                    return Resp(b"IMG")
                return Resp(json.dumps({"data": []}).encode())

        art = steam.steamgriddb_art("K", "Campaign Cartographer 3+", Opener())
        self.assertEqual(art, {"cover": (b"IMG", ".jpg")})
        wrong = Opener()
        wrong.title = "Cartoon Network Battle Crashers"  # autocomplete's closest match, but not the title
        self.assertEqual(steam.steamgriddb_art("K", "Campaign Cartographer 3+", wrong), {})
        self.assertTrue(all(a == "Bearer K" for u, a, _ in auth if u.startswith(steam.SGDB)))
        self.assertTrue(all(ua == steam.UA for *_, ua in auth))  # Cloudflare 403s urllib's default agent

    def test_fallback_art_uses_portrait_images_as_covers_and_square_ones_as_icons(self):
        import io
        import struct
        import zlib

        def png(w, h):
            return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
                    + struct.pack(">I", zlib.crc32(b"IHDR")))

        jpeg = b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"JF" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 900, 600)
        self.assertEqual(steam.image_size(png(600, 900)), (600, 900))
        self.assertEqual(steam.image_size(jpeg), (600, 900))
        self.assertIsNone(steam.image_size(b"GIF89a"))

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        images = {"https://i/wide": png(460, 215), "https://i/square": png(512, 512), "https://i/book": jpeg}

        class Opener:
            def open(self, req, timeout=None):
                return Resp(images[req.full_url])

        art = steam.art_from_images(["", "https://i/wide", "https://i/square", "https://i/book"], Opener())
        self.assertEqual(art, {"icon": (images["https://i/square"], ".png"), "cover": (jpeg, ".jpg")})
        self.assertEqual(steam.art_from_images(["https://i/wide"], Opener()), {})  # would stretch either way
        images["https://i/thumb"] = png(53, 70)
        self.assertEqual(steam.art_from_images(["https://i/thumb"], Opener()), {})  # too small for a cover

    def test_shortcuts_go_to_the_most_recent_login_only(self):
        root = self.cfg / "Steam"
        for uid in ("111", "222", "333"):
            (root / "userdata" / uid / "config").mkdir(parents=True)
        (root / "config").mkdir()
        base = steam.STEAMID64_BASE
        (root / "config/loginusers.vdf").write_text(steam.vdf_dumps({"users": {
            str(base + 111): {"AccountName": "me", "timestamp": "1792161784"},
            str(base + 222): {"AccountName": "sibling", "timestamp": "1748394314"}}}))
        self.assertEqual([c.parent.name for c in steam.target_config_dirs(root)], ["111"])
        self.assertEqual([c.parent.name for c in steam.target_config_dirs(root, "sibling")], ["222"])
        self.assertEqual([c.parent.name for c in steam.target_config_dirs(root, "333")], ["333"])
        (root / "config/loginusers.vdf").unlink()
        self.assertEqual(len(steam.target_config_dirs(root)), 3)  # nothing to go on: every account

    def test_notes_are_named_like_steams_and_listed_in_its_cloud_cache(self):
        import hashlib
        import json
        user = self.cfg / "userdata" / "123"
        (user / "config").mkdir(parents=True)
        (user / "632470").mkdir()
        (user / "632470/remotecache.vdf").write_text(steam.vdf_dumps({"632470": {"ChangeNumber": "5", "ostype": "-102"}}))
        path = steam.notes_path(user / "config", " Cave Story+ ")
        self.assertEqual(path, user / "2371090/remote/notes_shortcut_Cave_Story_")
        self.assertEqual(steam.notes_path(user / "config", "A [b] c^`{x}").name, "notes_shortcut_A__b__c__{x}")
        self.assertTrue(steam.add_note(user / "config", " Cave Story+ ", "From X", "[p]hi[/p]"))
        data = json.loads(path.read_text())
        self.assertEqual(data["shortcut_name"], "Cave Story+")
        [note] = data["notes"]
        self.assertEqual((note["id"], note["shortcut_name"], note["title"], note["content"]),
                         (steam.NOTE_ID, "Cave Story+", "From X", "[p]hi[/p]"))
        cache = steam.vdf_loads((user / "2371090/remotecache.vdf").read_text())["2371090"]
        self.assertEqual(cache["ostype"], "-102")  # this machine's, from another app's cache
        entry = cache[path.name]
        self.assertEqual((entry["syncstate"], entry["size"], entry["sha"]),  # a local change to upload
                         ("3", str(path.stat().st_size), hashlib.sha1(path.read_bytes()).hexdigest()))

    def test_add_note_keeps_the_users_notes_and_never_replaces_ours(self):
        import json
        config = self.cfg / "userdata" / "123" / "config"
        config.mkdir(parents=True)
        path = steam.notes_path(config, "Closure")
        path.parent.mkdir(parents=True)
        mine = {"id": "lx1", "shortcut_name": "Closure", "ordinal": 0, "title": "level 3 trick", "content": "[p]x[/p]"}
        path.write_text(json.dumps({"notes": [mine], "shortcut_name": "Closure"}))
        self.assertTrue(steam.add_note(config, "Closure", "From X", "[p]about[/p]"))
        notes = json.loads(path.read_text())["notes"]
        self.assertEqual([n["id"] for n in notes], ["lx1", steam.NOTE_ID])
        self.assertEqual(notes[1]["ordinal"], 1)
        notes[1]["content"] = "[p]edited by the user[/p]"
        path.write_text(json.dumps({"notes": notes, "shortcut_name": "Closure"}))
        self.assertFalse(steam.add_note(config, "Closure", "From X", "[p]about[/p]"))
        self.assertIn("edited by the user", path.read_text())
        self.assertTrue(list(path.parent.parent.glob("notes_shortcut_Closure.bak-*")))  # outside remote/

    def test_screenshots_go_in_the_shortcuts_own_folder_and_never_beside_the_users(self):
        import struct
        config = self.cfg / "userdata" / "123" / "config"
        config.mkdir(parents=True)
        index = self.cfg / "userdata/123/760/screenshots.vdf"
        index.parent.mkdir(parents=True)
        mine = {"0": {"type": "1", "filename": "1478500/screenshots/a.jpg", "gameid": "1478500"}}
        index.write_text(steam.vdf_dumps({"Screenshots": {"1478500": mine, "shortcutnames": {}}}))
        jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", 1080, 1920) + b"\x03" * 8
        sid, gid = 3873975362, str(steam.game_id(3873975362))
        self.assertFalse(steam.has_screenshots(config, sid))
        self.assertEqual(steam.add_screenshots(config, sid, "Offspring Fling", [(jpeg, b"thumb", "one"), (jpeg, None, "two")]), 2)
        self.assertTrue(steam.has_screenshots(config, sid))
        doc = steam.vdf_loads(index.read_text())["Screenshots"]
        self.assertEqual(list(doc), ["1478500", gid, "shortcutnames"])  # Steam keeps the names last
        self.assertEqual(doc["1478500"], mine)
        self.assertEqual(doc["shortcutnames"], {gid: "Offspring Fling"})
        first = doc[gid]["0"]
        self.assertEqual((first["width"], first["height"], first["caption"], first["hscreenshot"]),
                         ("1920", "1080", "one", steam.NOT_UPLOADED))
        folder = self.cfg / "userdata/123/760/remote" / gid / "screenshots"
        self.assertEqual((folder / first["filename"].split("/")[-1]).read_bytes(), jpeg)
        self.assertEqual((folder / "thumbnails" / first["filename"].split("/")[-1]).read_bytes(), b"thumb")
        self.assertEqual(steam.add_screenshots(config, sid, "Offspring Fling", [(jpeg, None, "three")]), 0)

    def test_removing_takes_out_only_what_we_added(self):
        import json
        import struct
        config = self.cfg / "Steam/userdata/123/config"
        config.mkdir(parents=True)
        jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", 1080, 1920) + b"\x03" * 8
        sid, gid = 3873975362, str(steam.game_id(3873975362))
        steam.add_screenshots(config, sid, "Offspring Fling", [(jpeg, None, f"one · {steam.FROM_STORE}"),
                                                               (jpeg, None, f"two · {steam.FROM_STORE}")])
        index = self.cfg / "Steam/userdata/123/760/screenshots.vdf"
        doc = steam.vdf_loads(index.read_text())
        doc["Screenshots"][gid]["2"] = {"type": "1", "filename": f"{gid}/screenshots/mine.jpg", "imported": "0",
                                        "caption": "", "hscreenshot": steam.NOT_UPLOADED}  # one the user took
        index.write_text(steam.vdf_dumps(doc))
        folder = index.parent / "remote" / gid / "screenshots"
        (folder / "mine.jpg").write_bytes(jpeg)
        self.assertEqual(steam.remove_screenshots(config, sid), 2)
        left = steam.vdf_loads(index.read_text())["Screenshots"]
        self.assertEqual([s["filename"] for s in left[gid].values()], [f"{gid}/screenshots/mine.jpg"])
        self.assertEqual(list(left), [gid, "shortcutnames"])
        self.assertEqual(sorted(p.name for p in folder.rglob("*.jpg")), ["mine.jpg"])
        self.assertEqual(steam.remove_screenshots(config, sid), 0)
        # Notes: ours goes (the file stays, emptied, for Steam Cloud to upload); an edited one stays.
        steam.add_note(config, "Offspring Fling", "From X", "[p]about[/p]")
        self.assertEqual(steam.remove_note(config, "Offspring Fling"), "removed")
        path = steam.notes_path(config, "Offspring Fling")
        self.assertEqual(json.loads(path.read_text())["notes"], [])
        cache = steam.vdf_loads((path.parent.parent / "remotecache.vdf").read_text())["2371090"]
        self.assertEqual(cache[path.name]["syncstate"], "3")
        steam.add_note(config, "Closure", "From X", "[p]about[/p]")
        path = steam.notes_path(config, "Closure")
        data = json.loads(path.read_text())
        data["notes"][0]["time_modified"] += 60
        path.write_text(json.dumps(data))
        self.assertEqual(steam.remove_note(config, "Closure"), "edited")
        self.assertEqual(steam.remove_note(config, "Shank 2"), "")
        # Artwork: every kind, and Steam's logo position beside it; another entry's stays.
        steam.write_artwork(config, sid, {"cover": (b"c", ".png"), "logo": (b"l", ".png"), "icon": (b"i", ".ico")})
        (config / "grid" / f"{sid}.json").write_text("{}")
        (config / "grid" / f"{sid}1_hero.png").write_bytes(b"other")
        self.assertEqual(steam.missing_art(config, sid), {"wide", "hero"})
        self.assertEqual(steam.remove_artwork(config, sid), 4)
        self.assertEqual([p.name for p in (config / "grid").iterdir()], [f"{sid}1_hero.png"])
        # Proton settings: ours go, the user's stay.
        (self.cfg / "Steam/config").mkdir()
        steam.set_compat_tool(self.cfg / "Steam", sid, "proton_9")
        steam.set_compat_tool(self.cfg / "Steam", 42, "proton_8")
        self.assertEqual(steam.remove_compat_tools(self.cfg / "Steam", [sid, 7]), 1)
        data = steam.vdf_loads((self.cfg / "Steam/config/config.vdf").read_text())
        self.assertEqual(list(data["InstallConfigStore"]["Software"]["Valve"]["Steam"]["CompatToolMapping"]), ["42"])

    def test_store_match_takes_the_same_name_or_a_prefix_by_the_same_maker(self):
        import io
        import json

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        search = {"Offspring Fling": [{"id": 1, "name": "Offspring Fling!"}],
                  "The Binding of Isaac + Wrath of the Lamb DLC": [],
                  "The Binding of Isaac": [{"id": 2, "name": "The Binding of Isaac: Rebirth"},
                                           {"id": 3, "name": "The Binding of Isaac"}],
                  "Snapshot": [{"id": 4, "name": "Snapshot"}, {"id": 5, "name": "SNAPSHOT"}]}
        devs = {1: ["Kyle Pulver"], 2: ["Nicalis"], 3: ["Edmund McMillen"], 4: ["Retro Affect"], 5: ["Someone"]}

        class Opener:
            def open(self, req, timeout=None):
                import urllib.parse
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
                if "term" in q:
                    return Resp(json.dumps({"items": search[q["term"][0]]}).encode())
                appid = int(q["appids"][0])
                return Resp(json.dumps({str(appid): {"success": True, "data": {
                    "steam_appid": appid, "developers": devs[appid]}}}).encode())

        match = lambda name, makers=(): (steam.store_match(name, makers, Opener()) or {}).get("steam_appid")
        self.assertEqual(match("Offspring Fling"), 1)  # a lone exact name needs no maker
        self.assertEqual(match("The Binding of Isaac + Wrath of the Lamb DLC", ["Edmund McMillen and Florian Himsl"]), 3)
        self.assertIsNone(match("The Binding of Isaac + Wrath of the Lamb DLC", ["Someone Else"]))
        self.assertEqual(match("Snapshot", ["Retro Affect"]), 4)
        self.assertIsNone(match("Snapshot"))  # two games of that name, nothing to tell them apart

    def test_owned_app_ids_reads_the_web_api_response(self):
        import io
        import json

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        seen = {}

        class Opener:
            def open(self, req, timeout=None):
                seen["url"], seen["ua"] = req.full_url, req.headers.get("User-agent")
                return Resp(json.dumps({"response": {"game_count": 2, "games": [
                    {"appid": 10}, {"appid": 620}]}}).encode())

        self.assertEqual(steam.owned_app_ids("KEY", 76561197960265729, Opener()), {10, 620})
        self.assertIn("steamid=76561197960265729", seen["url"])
        self.assertEqual(seen["ua"], steam.UA)
        self.assertEqual(steam.STEAMID64_BASE + 1, 76561197960265729)


if __name__ == "__main__":
    unittest.main()
