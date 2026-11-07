"""Seed a fake Humble account for screenshots and demos.

    python3 -I docs/demo/seed_demo_account.py DEMO_HOME
    HOME=DEMO_HOME python3 humble_integrator/cli.py plan

Writes cached orders and a keystore where `hi` looks for them under DEMO_HOME
(macOS: Library/Application Support/humble-integrator, Linux:
.local/share/humble-integrator). Every key is an obvious fake (DEMO-...);
games are invented, while the vendor items use real Humble machine names so
they resolve to their real recipes.
"""

import json
import os
import platform
import sys
from pathlib import Path

home = Path(sys.argv[1]).resolve()
data = (home / "Library" / "Application Support" if platform.system() == "Darwin"
        else home / ".local" / "share") / "humble-integrator"
(data / "orders").mkdir(parents=True, exist_ok=True)


def key(gamekey, machine_name, human_name, key_type="generic", value=None, steam_app_id=None,
        instructions="", bundle="", bundle_machine_name=""):
    return {"gamekey": gamekey, "keyindex": 0, "machine_name": machine_name, "human_name": human_name,
            "key_type": key_type, "key_type_human_name": key_type.title(), "bundle": bundle,
            "bundle_machine_name": bundle_machine_name, "steam_app_id": steam_app_id, "value": value,
            "is_gift": False, "is_expired": False, "instructions": instructions}


def download(platform_name, url, name="Download"):
    return {"platform": platform_name, "download_struct": [{"name": name, "url": {"web": url}, "md5": ""}]}


orders = [
    {"gamekey": "DEMOMAPS", "product": {"machine_name": "mapmaking_megabundle_encore",
                                        "human_name": "Map Making MEGA Bundle Encore"}, "subproducts": []},
    {"gamekey": "DEMOINDIE", "product": {"machine_name": "demo_indie_bundle", "human_name": "Demo Indie Bundle"},
     "subproducts": [
         {"machine_name": "starfall_tactics", "human_name": "Starfall Tactics", "downloads": [
             download("linux", "https://dl.example/starfall_tactics_x64.tar.gz", "64-bit .tar.gz"),
             download("mac", "https://dl.example/StarfallTactics.dmg"),
             download("windows", "https://dl.example/starfall_tactics_setup.exe")]},
         {"machine_name": "moss_and_lanterns", "human_name": "Moss & Lanterns", "downloads": [
             download("windows", "https://dl.example/moss_and_lanterns_setup.exe")]},
         {"machine_name": "starfall_tactics_soundtrack", "human_name": "Starfall Tactics Soundtrack",
          "downloads": [download("audio", "https://dl.example/starfall_ost_flac.zip", "FLAC")]},
     ]},
    {"gamekey": "DEMOBOOKS", "product": {"machine_name": "demo_book_bundle", "human_name": "Demo Book Bundle"},
     "subproducts": [{"machine_name": "the_cartographers_handbook", "human_name": "The Cartographer's Handbook",
                      "downloads": [download("ebook", "https://dl.example/cartographers_handbook.epub", "EPUB")]}]},
]
for o in orders:
    (data / "orders" / f"{o['gamekey']}.json").write_text(json.dumps(o))

maps = dict(bundle="Map Making MEGA Bundle Encore", bundle_machine_name="mapmaking_megabundle_encore")
keys = [
    key("DEMOMAPS", "campaigncartograher3pluslicenseextension", "Campaign Cartographer 3+ Lifetime License",
        value="DEMO-CC3P-0000-0001", **maps),
    key("DEMOMAPS", "dungeondesigner3", "Dungeon Designer 3", value="DEMO-DD3-0000-0002", **maps),
    key("DEMOMAPS", "cartographersannualvol17", "Cartographer's Annual Vol 17", **maps),
    key("DEMOINDIE", "echoes_of_the_deep_steam", "Echoes of the Deep", "steam", "DEMO1-AAAAA-BBBBB", 4000001,
        bundle="Demo Indie Bundle"),
    key("DEMOINDIE", "clockwork_courier_steam", "Clockwork Courier", "steam", None, 4000002,
        bundle="Demo Indie Bundle"),
    key("DEMOINDIE", "gardens_of_ash_epic", "Gardens of Ash", "epic", "DEMO-EPIC-0003", bundle="Demo Indie Bundle"),
    key("DEMOCOURSE", "mastering_python_automation", "Mastering Python Automation",
        value="DEMO-PACKT-0004", bundle="Python Automation Bundle by Packt",
        bundle_machine_name="pythonautomation_packt_softwarebundle"),
    key("DEMOASSET", "castle_kit_unity", "Castle Kit (Unity)", value="DEMO-UNITY-0005",
        instructions="Redeem at assetstore.unity.com", bundle="Demo Unity Asset Bundle"),
    key("DEMOINDIE", "humblemonthly_10percentoff_general", "10% off Humble Choice", bundle="Demo Indie Bundle"),
]
keystore = data / "keys.json"
keystore.write_text(json.dumps(keys, indent=1))
os.chmod(keystore, 0o600)
print(f"demo account seeded under {data}")
