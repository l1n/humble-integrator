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
            def open(self, req, timeout=None):
                url = req if isinstance(req, str) else req.full_url
                if not isinstance(req, str):
                    auth.append(req.headers.get("Authorization"))
                if "autocomplete" in url:
                    return Resp(json.dumps({"data": [{"id": 42}]}).encode())
                if "/grids/game/42?dimensions=600x900" in url:
                    return Resp(json.dumps({"data": [{"url": "https://cdn/x/cover.jpg"}]}).encode())
                if url.startswith("https://cdn/"):
                    return Resp(b"IMG")
                return Resp(json.dumps({"data": []}).encode())

        art = steam.steamgriddb_art("K", "Campaign Cartographer 3+", Opener())
        self.assertEqual(art, {"cover": (b"IMG", ".jpg")})
        self.assertTrue(all(a == "Bearer K" for a in auth))

    def test_owned_app_ids_reads_the_web_api_response(self):
        import io
        import json

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        seen = {}

        class Opener:
            def open(self, url, timeout=None):
                seen["url"] = url
                return Resp(json.dumps({"response": {"game_count": 2, "games": [
                    {"appid": 10}, {"appid": 620}]}}).encode())

        self.assertEqual(steam.owned_app_ids("KEY", 76561197960265729, Opener()), {10, 620})
        self.assertIn("steamid=76561197960265729", seen["url"])
        self.assertEqual(steam.STEAMID64_BASE + 1, 76561197960265729)


if __name__ == "__main__":
    unittest.main()
