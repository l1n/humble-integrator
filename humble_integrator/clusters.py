"""Cut the census into per-cluster extracts for the vendor-recipe subagents.

A bundle row goes to a cluster when its redemption text, slug, or any item's
developer/publisher matches the cluster's pattern; only that bundle's
not-yet-covered items are kept.

    python3 -I humble_integrator/clusters.py --census data/census.jsonl --out data/clusters
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recipes  # noqa: E402

CLUSTERS = {
    "courses-packt-zenva": r"packt|aci learning|zenva",
    "courses-mammoth-gamedevtv": r"mammoth|gamedev\.tv|gamedevtv|fasttrack|janets|mayfair|flippednormals|udemy|"
                                 r"richdale|movie maestros",
    "engine-asset-stores": r"unity|\bfab\b|unreal|synty|leartes|cosmos|hivemind|dekogon|meshingun|eldamar|artngame|"
                           r"stylarts|befour|gabriel aguiar|ragtarock|polysquid|kitbash|quixel",
    "marketplaces": r"gamedev ?market|gumroad|jro ?tools|scansmatter|anything world|rexard|itch\.io|artstation",
    "audio": r"ovani|audio ?hero|applied acoustics|wa productions|function loops|apprehension|sonniss|"
             r"sound ?fx|soundtrack|royalty.free music|sample library|loops",
    "3d-print": r"highcraft|next level miniatures|myminifactory|sculptiq|lyoshko|stl|miniature|3d.print",
    "desktop-software": r"ashampoo|movavi|escape motions|rebelle|anthropics|moho|lost marble|paintstorm|cadsoft|"
                        r"archimaster|corel|serif|affinity|cyberlink|wondershare|bitdefender|norton|mcafee|"
                        r"acronis|magix|vegas|aomei|abelssoft|clip studio|celsys|krita|zbrush|substance",
}


# Wave 2: what wave 1 left uncovered in the software census.
WAVE2 = {
    "w2-leartes-hivemind": r"leartes|cosmos|stylarts|hivemind|no mad studios|ursa\.animation|akairo|switchboard studios",
    "w2-fab-unity-residue": r"epic games|\bepic\b|\bfab\b|unity|bugrimov|dekogon|meshingun|yarrawah|velarion|freshcan|"
                            r"laya design|danstudios|infinity pbr|nature ?manufacture|magic pig|monument studio|"
                            r"3d tudor|kitbash3d|rutkovskyi",
    "w2-3d-print": r"hayland|albinoraven|bard's tale|bards tale|3d.print|miniature|\bstl\b",
    "w2-audio": r"ik multimedia|soundtrack loops|pegonthetrack|cherry audio|producer loops|krotos|acoustica|"
                r"emediamusic|khron studio|noise alchemy|loop crate|music producer|sound designer",
    "w2-learning-apps": r"transparent language|dailyom|informit|pearson|skillademia|talk python|hero ?lab|lone wolf|"
                        r"yoyo games|gamemaker|virtual architect|dragonsan|getrekt|game assembly|penny de byl|"
                        r"michael bridges|lightstream|botisimo|lastpass|markt ?\+ ?technik|beckercad",
}


# Wave 3a: what's left in software after the legacy-format (2017-2020) bundles landed.
WAVE3 = {
    "w3-engine-assets": r"vrhinofx|dekogon|laya design|yarrawah|hivemind|befour|gabriel aguiar|infinity pbr|ragtarock|"
                        r"entroverse|sics games|leartes|shapelab|mattepaint|actionvfx|unreal|\bunity\b|\bfab\b|epic",
    "w3-2d-marketplaces": r"gamedev ?market|rexard|pv games|evilmind|green fields|rpg maker|mammoth templates|synty|"
                          r"elvgames|itch\.io|pixel|godot|2d game",
    "w3-learning-apps": r"skillet|royal open college|lonely planet|reallusion|skylum|luminar|course|academy|learn",
    "w3-ttrpg-vtt": r"smiteworks|fantasy grounds|pelgrane|cal moore|treasure maps|hayland|albinoraven|3d.print|"
                    r"pathfinder|\brpg\b|tabletop|dungeon",
}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--census", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--wave", type=int, default=1)
    p.add_argument("--category", choices=("software", "games", "books"))
    a = p.parse_args(argv)
    clusters = {1: CLUSTERS, 2: WAVE2, 3: WAVE3}[a.wave]
    book = recipes.load()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = {name: [] for name in clusters}
    for line in open(a.census):
        b = json.loads(line)
        if a.category and b["category"] != a.category:
            continue
        left = [it for it in b["items"] if book.resolve(it, b)[0] is None]
        if not left:
            continue
        hay_bundle = " ".join([b["slug"], b.get("redeem_text", "")])
        for name, pat in clusters.items():
            hits = [it for it in left if re.search(pat, hay_bundle + " " + " ".join(
                n for n in (it.get("developers") or []) + (it.get("publishers") or []) if n), re.I)]
            if hits:
                rows[name].append(dict(b, items=hits))
    for name, bundles in rows.items():
        with open(out / f"{name}.jsonl", "w") as f:
            for b in bundles:
                f.write(json.dumps(b) + "\n")
        print(f"{name:28} {len(bundles):4d} bundles {sum(len(b['items']) for b in bundles):6d} items")


if __name__ == "__main__":
    main()
