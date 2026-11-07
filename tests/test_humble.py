import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import humble  # noqa: E402

# Shaped like /api/v1/order/<gamekey>?all_tpkds=true: one Steam key already
# revealed, one Steam key not yet revealed, one vendor key for a whole bundle.
ORDER = {
    "gamekey": "GK1",
    "product": {"human_name": "Map Making MEGA Bundle Encore", "machine_name": "mapmaking_encore_bundle"},
    "tpkd_dict": {"all_tpks": [
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "somegame_steam", "human_name": "Some Game",
         "key_type": "steam", "key_type_human_name": "Steam", "steam_app_id": 111,
         "redeemed_key_val": "AAAAA-BBBBB-CCCCC", "is_gift": False, "is_expired": False},
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "owned_steam", "human_name": "Owned Game",
         "key_type": "steam", "key_type_human_name": "Steam", "steam_app_id": 222},
        {"gamekey": "GK1", "keyindex": 0, "machine_name": "profantasy_mapmaking_key",
         "human_name": "ProFantasy bundle key", "key_type": "generic", "key_type_human_name": "Key",
         "steam_app_id": None, "custom_instructions_html": "Redeem at <a href='https://www.profantasy.com'>"},
    ]},
}


class FakeClient:
    def __init__(self, orders):
        self.orders = orders
        self.revealed = []

    def gamekeys(self):
        return list(self.orders)

    def order(self, gk):
        return self.orders[gk]

    def reveal(self, key):
        self.revealed.append(key.machine_name)
        return f"REVEALED-{key.machine_name}"


class KeysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "keys.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_parses_every_key_type_from_an_order(self):
        keys = list(humble.keys_in_order(ORDER))
        self.assertEqual([k.key_type for k in keys], ["steam", "steam", "generic"])
        self.assertEqual(keys[0].value, "AAAAA-BBBBB-CCCCC")
        self.assertFalse(keys[1].revealed)
        self.assertIn("profantasy.com", keys[2].instructions)
        self.assertEqual(keys[2].bundle, "Map Making MEGA Bundle Encore")

    def test_default_extract_reveals_nothing(self):
        client = FakeClient({"GK1": ORDER})
        store = humble.extract(client, humble.KeyStore(self.store_path), log=lambda *_: None)
        self.assertEqual(client.revealed, [])
        self.assertEqual(sum(k.revealed for k in store.keys.values()), 1)

    def test_needed_skips_steam_games_already_owned(self):
        client = FakeClient({"GK1": ORDER})
        humble.extract(client, humble.KeyStore(self.store_path), policy="needed",
                       steam_owned=frozenset({222}), log=lambda *_: None)
        self.assertEqual(client.revealed, ["profantasy_mapmaking_key"])

    def test_needed_without_ownership_info_leaves_steam_keys_alone(self):
        client = FakeClient({"GK1": ORDER})
        humble.extract(client, humble.KeyStore(self.store_path), policy="needed", log=lambda *_: None)
        self.assertEqual(client.revealed, ["profantasy_mapmaking_key"])

    def test_type_filter_limits_reveals(self):
        client = FakeClient({"GK1": ORDER})
        humble.extract(client, humble.KeyStore(self.store_path), policy="all", types={"steam"},
                       log=lambda *_: None)
        self.assertEqual(client.revealed, ["owned_steam"])

    def test_keystore_is_private_and_never_forgets_a_value(self):
        humble.extract(FakeClient({"GK1": ORDER}), humble.KeyStore(self.store_path), policy="all",
                       log=lambda *_: None)
        self.assertEqual(stat.S_IMODE(self.store_path.stat().st_mode), 0o600)
        # A later run where Humble returns the key without its value keeps the stored one.
        stripped = json.loads(json.dumps(ORDER))
        for tpk in stripped["tpkd_dict"]["all_tpks"]:
            tpk.pop("redeemed_key_val", None)
        again = FakeClient({"GK1": stripped})
        store = humble.extract(again, humble.KeyStore(self.store_path), policy="all", log=lambda *_: None)
        self.assertTrue(all(k.revealed for k in store.keys.values()))
        self.assertEqual(again.revealed, [], "stored keys must not be revealed a second time")

    def test_downloads_and_build_choice(self):
        order = {"gamekey": "GK2", "subproducts": [{"machine_name": "game1", "human_name": "Game 1", "downloads": [
            {"platform": "windows", "download_struct": [
                {"name": "Download", "url": {"web": "https://dl.humble.com/game1_setup.exe?t=1"}, "md5": "a"}]},
            {"platform": "linux", "download_struct": [
                {"name": "32-bit .deb", "url": {"web": "https://dl.humble.com/game1_i386.deb"}},
                {"name": "64-bit .tar.gz", "url": {"web": "https://dl.humble.com/game1_x64.tar.gz"}},
                {"name": "torrent only", "url": {}}]},
        ]}]}
        dls = list(humble.downloads_in_order(order))
        self.assertEqual(len(dls), 3)
        self.assertEqual(dls[0].filename, "game1_setup.exe")
        self.assertEqual([d.filename for d in humble.pick_build(dls, "Linux")], ["game1_x64.tar.gz"])
        self.assertEqual([d.platform for d in humble.pick_build(dls, "Darwin")], ["windows"])

    def test_fetch_resumes_and_checks_md5(self):
        import hashlib, io
        body = b"0123456789" * 1000
        md5 = hashlib.md5(body).hexdigest()

        class Resp(io.BytesIO):
            status = 206
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Opener:
            def open(self, req, timeout=None):
                start = int(req.headers.get("Range", "bytes=0-")[6:-1] or 0)
                return Resp(body[start:])

        d = humble.Download("game1", "Game 1", "linux", "x", "https://dl/game1.tar.gz", md5=md5)
        dest = Path(self.tmp.name) / "dl"
        dest.mkdir()
        (dest / "game1.tar.gz.part").write_bytes(body[:4000])  # interrupted earlier
        out = humble.fetch(d, dest, Opener())
        self.assertEqual(out.read_bytes(), body)
        bad = humble.Download("game1", "Game 1", "linux", "x", "https://dl/other.tar.gz", md5="0" * 32)
        with self.assertRaises(RuntimeError):
            humble.fetch(bad, dest, Opener())
        self.assertFalse((dest / "other.tar.gz.part").exists())

    def test_mask_hides_most_of_the_key(self):
        self.assertEqual(humble.mask("AAAAA-BBBBB-CCCCC"), "AAAA…CC")
        self.assertEqual(humble.mask(None), "(unrevealed)")


if __name__ == "__main__":
    unittest.main()
