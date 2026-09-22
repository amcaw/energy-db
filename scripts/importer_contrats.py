import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SORTIE = os.path.join(RACINE, "data")
DEFAUT = os.path.join(os.path.dirname(RACINE), "svelte_energy", "static", "donnees",
                      "barometre.json")
MANIFESTE = os.path.join(os.path.dirname(RACINE), "svelte_energy", "fiches",
                         "comparateur", "manifeste.csv")


HORODATAGE_MEDIA = re.compile(r"/([0-9a-f]{8})[0-9a-f]*\.pdf")


def parue_le(url):
    m = HORODATAGE_MEDIA.search(url or "")
    if not m:
        return ""
    horodatage = datetime.fromtimestamp(int(m.group(1), 16), timezone.utc)
    return horodatage.date().isoformat()


def publications():
    if not os.path.exists(MANIFESTE):
        return {}
    with open(MANIFESTE, newline="", encoding="utf-8") as f:
        return {r["fournisseur"] + "/" + r["fichier"]: r["url"]
                for r in csv.DictReader(f) if r.get("url")}


def slug(*bouts):
    t = "-".join(str(b) for b in bouts if b)
    t = t.lower().replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    t = t.replace("ô", "o").replace("û", "u").replace("ç", "c").replace("î", "i")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return re.sub(r"-{2,}", "-", t).strip("-")


def formule(f):
    return {
        "flux": f["flux"],
        "usage": f["usage"],
        "a": f["a"],
        "b": f["b"],
        "indice_fiche": f.get("indice", ""),
        "serie": f.get("serie", ""),
        "rapport": f.get("rapport", 1.0),
        "tva_pct": f.get("tva", 0.0),
        "ligne_source": f.get("source", ""),
    }


def convertir(c, urls=None):
    out = {
        "cle": slug(c["fournisseur"], c["energie"], c["region"], c["signature"],
                    c.get("produit_source") or c["produit"], c.get("type"), c.get("duree")),
        "fournisseur": c["fournisseur"],
        "energie": c["energie"],
        "region": c["region"],
        "signature": c["signature"],
        "produit": c.get("produit_source") or c["produit"],
        "produit_affiche": c["produit"],
        "type": c.get("type", ""),
        "duree": c.get("duree", ""),
        "lisible": not c.get("muet", False),
        "prix_fixe": bool(c.get("fixe", False)),
        "periodicite": c.get("periodicite", "mensuelle"),
        "tva_pct": c.get("tva", 0.0),
        "tvac": bool(c.get("tvac", False)),
        "indice_fiche": c.get("indice", ""),
        "serie": c.get("serie", ""),
        "formules": [formule(f) for f in c.get("formules", [])],
    }
    if c.get("base_tva"):
        out["base_tva"] = c["base_tva"]
    if c.get("redevance") is not None:
        out["redevance_eur_an"] = c["redevance"]
    if c.get("muet"):
        out["raison_non_lue"] = c.get("raison", "")
    if c.get("fiche"):
        out["fiche"] = c["fiche"]
        lien = (urls or {}).get(c["fiche"])
        if lien:
            out["fiche_url"] = lien
            paru = parue_le(lien)
            if paru:
                out["fiche_parue_le"] = paru
    return out


def ecrire(chemin, horodatage, contrats, source):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"genere_le": horodatage, "source": source,
                   "prix": "prix_c_eur_kwh = (a * indice * rapport + b) * (1 + tva_pct / 100)",
                   "contrats": contrats}, f, ensure_ascii=False, indent=1)


def main(chemin=DEFAUT):
    if not os.path.exists(chemin):
        print(f"introuvable : {chemin}")
        return 1
    paquet = json.load(open(chemin, encoding="utf-8"))
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    urls = publications()
    contrats = [convertir(c, urls) for c in paquet["contrats"]]
    contrats.sort(key=lambda c: (c["energie"], c["fournisseur"], c["produit"],
                                 c["signature"]))
    vus, uniques = set(), []
    for c in contrats:
        if c["cle"] in vus:
            c["cle"] = f"{c['cle']}-{len(vus)}"
        vus.add(c["cle"])
        uniques.append(c)
    os.makedirs(SORTIE, exist_ok=True)
    source = f"barometre des tarifs variables, extraction du {paquet.get('genere_le', '')[:10]}"
    ecrire(os.path.join(SORTIE, "contrats.json"), horodatage, uniques, source)
    gaz = [c for c in uniques if c["energie"] == "gaz"]
    ecrire(os.path.join(SORTIE, "contrats_gaz.json"), horodatage, gaz, source)
    lisibles = [c for c in uniques if c["lisible"]]
    print(json.dumps({
        "source": chemin,
        "contrats": len(uniques),
        "lisibles": len(lisibles),
        "dont_prix_fixe": sum(1 for c in lisibles if c["prix_fixe"]),
        "gaz": len(gaz),
        "electricite": len(uniques) - len(gaz),
        "fournisseurs": sorted({c["fournisseur"] for c in uniques}),
        "signatures": f"{min(c['signature'] for c in uniques)} -> "
                      f"{max(c['signature'] for c in uniques)}",
        "formules": sum(len(c["formules"]) for c in uniques),
        "avec_lien_fiche": sum(1 for c in uniques if c.get("fiche_url")),
        "gaz_avec_lien_fiche": sum(1 for c in gaz if c.get("fiche_url")),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAUT))
