import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OBS = os.path.join(DATA, "observations.csv")
PROFILS = os.path.join(DATA, "profils")
ENCOURS = os.path.join(DATA, "indices_derives_encours.json")
ENTETE_OBS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]

SERIES = {
    "ZTP DAM RLP": {"energie": "gaz", "jours": "ztp_jours.csv", "cle": "valeur", "pondere": True,
                    "trimestriel": False,
                    "source": "EEX EGSI ZTP Day + Weekend pondere par le profil gaz Synergrid"},
    "TTF DAM RLP": {"energie": "gaz", "jours": "ttf_jours.csv", "cle": "valeur", "pondere": True,
                    "trimestriel": False,
                    "source": "EEX EGSI TTF Day + Weekend pondere par le profil gaz Synergrid"},
    "Epex DAM trimestriel": {"energie": "electricite", "jours": "epex_jours.csv", "cle": "moyenne",
                             "pondere": False, "trimestriel": True,
                             "source": "moyenne trimestrielle des prix journaliers Epex DAM (energy-charts.info)"},
}

REFERENCE = {
    "ZTP DAM RLP": {"2025-11": 29.959, "2025-12": 27.805, "2026-01": 34.548, "2026-02": 32.696,
                    "2026-03": 50.642, "2026-06": 44.772, "2026-07": 53.043, "2026-08": 61.899},
    "TTF DAM RLP": {"2025-11": 30.52, "2025-12": 27.652, "2026-01": 34.058, "2026-02": 33.296,
                    "2026-03": 51.264, "2026-06": 45.076, "2026-07": 53.075, "2026-08": 61.729},
    "Epex DAM trimestriel": {"2025-09": 71.97, "2025-12": 82.19, "2026-03": 95.75, "2026-06": 94.25},
}
TOLERANCE_REFERENCE = 0.05


def lire(nom, cle):
    chemin = os.path.join(DATA, nom)
    if not os.path.exists(chemin):
        return {}
    return {r["jour"]: float(r[cle]) for r in csv.DictReader(open(chemin, encoding="utf-8"))}


def profil():
    out = {}
    for nom in sorted(os.listdir(PROFILS)):
        if nom.startswith("rlp_gaz_") and nom.endswith(".json"):
            out.update(json.load(open(os.path.join(PROFILS, nom), encoding="utf-8")))
    return out


def trimestre(mois):
    an, mo = int(mois[:4]), int(mois[5:7])
    premier = (mo - 1) // 3 * 3 + 1
    return [f"{an}-{m:02d}" for m in range(premier, premier + 3)]


def jours_dans(mois):
    an, mo = int(mois[:4]), int(mois[5:7])
    return (date(an + (mo == 12), mo % 12 + 1, 1) - date(an, mo, 1)).days


def calculer(spec, jours, poids):
    par_mois = defaultdict(dict)
    for jour, valeur in jours.items():
        par_mois[jour[:7]][jour] = valeur
    resultats = {}
    for mois in sorted(par_mois):
        periode = trimestre(mois) if spec["trimestriel"] else [mois]
        valeurs = {j: v for m in periode for j, v in par_mois.get(m, {}).items()}
        attendus = sum(jours_dans(m) for m in periode)
        if spec["pondere"]:
            if any(j not in poids for j in valeurs):
                continue
            total = sum(poids[j] for j in valeurs)
            valeur = sum(v * poids[j] for j, v in valeurs.items()) / total
        else:
            valeur = statistics.fmean(valeurs.values())
        resultats[mois] = {"valeur": valeur, "jours": len(valeurs), "attendus": attendus,
                           "complet": len(valeurs) >= attendus}
    return resultats


def controle(nom, resultats):
    ecarts = []
    for mois, attendu in REFERENCE.get(nom, {}).items():
        r = resultats.get(mois)
        if r and r["complet"] and abs(r["valeur"] - attendu) > TOLERANCE_REFERENCE:
            ecarts.append({"indice": nom, "mois": mois, "calcule": round(r["valeur"], 3), "publie": attendu})
    return ecarts


def cmd_publier():
    poids = profil()
    connus = {}
    if os.path.exists(OBS):
        for r in csv.DictReader(open(OBS, encoding="utf-8")):
            connus[(r["indice"], r["mois"])] = r["valeur"]
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ajouts, ecarts, encours = [], [], {}
    for nom, spec in SERIES.items():
        resultats = calculer(spec, lire(spec["jours"], spec["cle"]), poids)
        ecarts += controle(nom, resultats)
        for mois, r in resultats.items():
            if not r["complet"]:
                encours[nom] = {"mois": mois, "valeur": round(r["valeur"], 4), "jours": r["jours"],
                                "attendus": r["attendus"]}
                continue
            texte = f"{r['valeur']:.4f}"
            if connus.get((nom, mois)) == texte:
                continue
            ajouts.append({"observed_at": horodatage, "energie": spec["energie"], "mois": mois,
                           "indice": nom, "valeur": texte, "source": spec["source"], "sha256": ""})
    if ecarts:
        print(json.dumps({"erreur": "ecart avec les valeurs publiees par les fournisseurs",
                          "ecarts": ecarts}, ensure_ascii=False, indent=2))
        return 1
    if ajouts:
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=ENTETE_OBS).writerows(ajouts)
    mois_courant = datetime.now(BE).strftime("%Y-%m")
    fichier = {"mois": mois_courant, "calcule_le": horodatage, "series": {}}
    for nom, e in encours.items():
        if e["mois"] == mois_courant or nom in ("Epex DAM trimestriel",):
            fichier["series"][nom] = e
    with open(ENCOURS, "w", encoding="utf-8") as f:
        json.dump(fichier, f, ensure_ascii=False, indent=1)
    print(json.dumps({"valeurs_ajoutees": len(ajouts), "en_cours": fichier["series"]},
                     ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"publier": cmd_publier}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "publier"
    if nom not in COMMANDES:
        print(f"usage: indices_derives.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
