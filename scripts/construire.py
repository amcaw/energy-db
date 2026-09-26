import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "collecte"))

DATA = os.path.join(RACINE, "collecte", "data")
SORTIE = os.path.join(RACINE, "data")
OBS = os.path.join(DATA, "observations.csv")
ENCOURS = os.path.join(DATA, "indices_profil_encours.json")

QUOTIDIEN = {"Epex DAM": "epex_jours.csv", "ZTP DAM": "ztp_jours.csv",
             "TTF DAM": "ttf_jours.csv"}

META = {
    "Epex DAM": {"energie": "electricite", "role": "comptant",
                 "libelle": "Epex SPOT Belgium, Day Ahead",
                 "unite": "EUR/MWh",
                 "note": "Moyenne des moyennes journalieres du marche belge de l'electricite. "
                         "Connue a la fin du mois. C'est l'ancien Belpex.",
                 "source": "energy-charts.info (Fraunhofer ISE), recoupe avec SMARD"},
    "Epex DAM RLP": {"energie": "electricite", "role": "comptant",
                     "libelle": "Epex SPOT Belgium pondere par le profil de consommation RLP0N",
                     "unite": "EUR/MWh",
                     "note": "Prix quart-horaires ponderes par le profil RLP0N, moyenne "
                             "arithmetique de tous les gestionnaires de reseau belges.",
                     "source": "energy-charts.info + profil Synergrid RLP0N"},
    "Epex DAM SPP": {"energie": "electricite", "role": "comptant",
                     "libelle": "Epex SPOT Belgium pondere par le profil de production solaire SPP",
                     "unite": "EUR/MWh",
                     "note": "Prix quart-horaires ponderes par le profil SPP. C'est le parametre "
                             "usuel des contrats d'injection photovoltaique.",
                     "source": "energy-charts.info + profil Synergrid SPP"},
    "Endex 101": {"energie": "electricite", "role": "terme",
                  "libelle": "ICE Endex 101, produit mensuel belge",
                  "unite": "EUR/MWh",
                  "note": "Prix a terme cote le mois precedent pour livraison le mois suivant.",
                  "source": "parametres d'indexation publies par Engie, recoupes avec EEX"},
    "Endex 103": {"energie": "electricite", "role": "terme",
                  "libelle": "ICE Endex 103, produit trimestriel", "unite": "EUR/MWh",
                  "note": "Moyenne des cotations du trimestre precedent.",
                  "source": "parametres d'indexation publies par Engie"},
    "Endex 303": {"energie": "electricite", "role": "terme",
                  "libelle": "ICE Endex 303", "unite": "EUR/MWh",
                  "note": "Produit a terme trimestriel, historique.",
                  "source": "parametres d'indexation publies par Engie"},
    "ZTP DAM": {"energie": "gaz", "role": "comptant",
                "libelle": "ZTP EGSI Day Ahead + Weekend", "unite": "EUR/MWh",
                "note": "Moyenne des cotations journalieres du hub gazier belge.",
                "source": "EEX EGSI ZTP Day"},
    "ZTP 101": {"energie": "gaz", "role": "terme",
                "libelle": "ZTP 101, produit mensuel a terme", "unite": "EUR/MWh",
                "note": "Prix a terme du hub belge, cote le mois precedent.",
                "source": "parametres d'indexation publies par Engie, recoupes avec EEX"},
    "TTF DAM": {"energie": "gaz", "role": "comptant",
                "libelle": "TTF EGSI Day Ahead + Weekend", "unite": "EUR/MWh",
                "note": "Moyenne des cotations journalieres du hub gazier neerlandais.",
                "source": "EEX EGSI TTF Day + Weekend"},
    "TTF 101 Heren": {"energie": "gaz", "role": "terme",
                      "libelle": "TTF 101, produit mensuel a terme", "unite": "EUR/MWh",
                      "note": "Prix a terme du hub neerlandais, cote le mois precedent.",
                      "source": "parametres d'indexation publies par Engie"},
    "TTF 103 (Heren)": {"energie": "gaz", "role": "terme",
                        "libelle": "TTF 103, produit trimestriel", "unite": "EUR/MWh",
                        "note": "Moyenne des cotations du trimestre precedent.",
                        "source": "parametres d'indexation publies par Engie"},
}
COMPTANT = {"Epex DAM", "Epex DAM RLP", "Epex DAM SPP", "ZTP DAM", "TTF DAM"}


def jours_du_mois(m):
    a, mo = int(m[:4]), int(m[5:7])
    return (date(a + (mo == 12), mo % 12 + 1, 1) - date(a, mo, 1)).days


def lire(nom):
    chemin = os.path.join(DATA, nom)
    if not os.path.exists(chemin):
        return []
    return list(csv.DictReader(open(chemin, encoding="utf-8")))


def mensuel():
    out = defaultdict(dict)
    for r in lire("observations.csv"):
        if len(r.get("mois", "")) != 7:
            continue
        try:
            out[r["indice"]][r["mois"]] = float(r["valeur"])
        except (TypeError, ValueError):
            continue
    return out


def quotidien(nom):
    par_jour = {}
    for r in lire(nom):
        jour = r.get("jour")
        try:
            v = float(r.get("moyenne") or r.get("valeur"))
        except (TypeError, ValueError):
            continue
        if jour:
            par_jour[jour] = v
    return par_jour


def estime_profil(indice):
    if not os.path.exists(ENCOURS):
        return None
    d = json.load(open(ENCOURS, encoding="utf-8"))
    v = d.get(indice)
    if v is None:
        return None
    return {"mois": d["mois"], "valeur": v, "jours_connus": d["jours_connus"],
            "jours_total": jours_du_mois(d["mois"]), "definitif": False}


def estime(indice, publies):
    fichier = QUOTIDIEN.get(indice)
    if not fichier:
        n = estime_profil(indice)
        return n if n and n["mois"] not in publies else None
    par_jour = quotidien(fichier)
    if not par_jour:
        return None
    par_mois = defaultdict(list)
    for jour, v in sorted(par_jour.items()):
        par_mois[jour[:7]].append(v)
    mois = max(par_mois)
    if mois in publies:
        return None
    return {"mois": mois, "valeur": round(statistics.fmean(par_mois[mois]), 4),
            "jours_connus": len(par_mois[mois]), "jours_total": jours_du_mois(mois),
            "definitif": False}


def main():
    os.makedirs(SORTIE, exist_ok=True)
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    series = mensuel()
    indices = {}
    for nom in sorted(set(series) | set(META)):
        publies = series.get(nom, {})
        if not publies:
            continue
        bloc = {"comptant": nom in COMPTANT,
                **META.get(nom, {"energie": "", "role": "", "libelle": nom,
                                 "unite": "EUR/MWh", "note": "", "source": ""}),
                "premier_mois": min(publies), "dernier_mois": max(publies),
                "mois_publies": len(publies),
                "publie": dict(sorted(publies.items()))}
        n = estime(nom, publies)
        if n:
            bloc["estime"] = n
        indices[nom] = bloc

    with open(os.path.join(SORTIE, "indices.json"), "w", encoding="utf-8") as f:
        json.dump({"genere_le": horodatage, "unite": "EUR/MWh", "indices": indices},
                  f, ensure_ascii=False, indent=1)

    lignes = []
    for indice, fichier in sorted(QUOTIDIEN.items()):
        for jour, v in sorted(quotidien(fichier).items()):
            lignes.append({"jour": jour, "indice": indice, "valeur": f"{v:.4f}"})
    with open(os.path.join(SORTIE, "indices_jours.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["jour", "indice", "valeur"])
        w.writeheader()
        w.writerows(sorted(lignes, key=lambda x: (x["indice"], x["jour"])))

    offres = 0
    chemin = os.path.join(SORTIE, "cwape_gaz.json")
    if os.path.exists(chemin):
        offres = len(json.load(open(chemin, encoding="utf-8")).get("offres_actuelles", []))

    profondeur = {}
    for l in lignes:
        d = profondeur.setdefault(l["indice"], {"jours": 0, "premier": l["jour"],
                                                "dernier": l["jour"]})
        d["jours"] += 1
        d["premier"] = min(d["premier"], l["jour"])
        d["dernier"] = max(d["dernier"], l["jour"])

    meta = {"genere_le": horodatage,
            "indices": len(indices),
            "series_quotidiennes": dict(sorted(profondeur.items())),
            "valeurs_mensuelles": sum(len(b["publie"]) for b in indices.values()),
            "valeurs_quotidiennes": len(lignes),
            "offres_cwape_gaz": offres,
            "fichiers": ["indices.json", "indices_jours.csv", "cwape_gaz.json", "meta.json"]}
    with open(os.path.join(SORTIE, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(json.dumps(meta, ensure_ascii=False, indent=1))
    for nom, b in sorted(indices.items()):
        n = b.get("estime")
        print(f"  {nom:16s} {b['mois_publies']:3d} mois  {b['premier_mois']} -> "
              f"{b['dernier_mois']}" + (f" | en cours {n['mois']} {n['valeur']}" if n else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
