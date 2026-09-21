import calendar
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

API = "https://api.eex-group.com/pub/market-data/chart/eod"
BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
JOURS = os.path.join(DATA, "terme_jours.csv")
OBS = os.path.join(DATA, "observations.csv")
CHAMPS = ["jour", "indice", "echeance", "valeur", "releve_le"]

INDICES = {
    "Endex 101": {
        "commodity": "POWER", "area": "BE", "product": "Base",
        "pricing": "F", "shortCode": "Q1BM",
        "engie": ("electricite", "Endex 101"),
    },
    "TTF 101": {
        "commodity": "NATGAS", "area": "TTF", "product": "Base",
        "pricing": "F", "shortCode": "G3BM",
        "engie": ("gaz", "TTF 101 Heren"),
    },
    "ZTP 101": {
        "commodity": "NATGAS", "area": "ZTP", "product": "Base",
        "pricing": "F", "shortCode": "GBBM",
        "engie": ("gaz", "ZTP 101"),
    },
}
HORIZON = 4


def decale(mois, n):
    an, mo = (int(x) for x in mois.split("-"))
    mo += n
    an += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{an}-{mo:02d}"


def bornes(mois):
    an, mo = (int(x) for x in mois.split("-"))
    return f"{mois}-01", f"{mois}-{calendar.monthrange(an, mo)[1]:02d}"


def mois_courant():
    a = datetime.now(BE).date()
    return f"{a.year}-{a.month:02d}"


def fetch(spec, echeance, debut, fin, essais=5):
    params = {
        "commodity": spec["commodity"], "area": spec["area"],
        "product": spec["product"], "pricing": spec["pricing"],
        "shortCode": spec["shortCode"], "maturity": echeance.replace("-", ""),
        "startDate": debut, "endDate": fin,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    attente = 4
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "Referer": "https://eds.eex-group.com/",
                "User-Agent": "barometre-energie/1.0 (redaction)",
            })
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                return {}
            if n == essais - 1:
                raise
            time.sleep(attente)
            attente *= 2
        except urllib.error.URLError:
            if n == essais - 1:
                raise
            time.sleep(attente)
            attente *= 2
    out = {}
    for serie in d.get("series", []):
        if serie.get("serieName") != "settlPx":
            continue
        for ligne in serie.get("timeAndValue", []):
            if len(ligne) >= 2 and ligne[1] is not None:
                out[ligne[0]] = float(ligne[1])
    return out


def lire():
    out = {}
    if os.path.exists(JOURS):
        for r in csv.DictReader(open(JOURS, encoding="utf-8")):
            out[(r["jour"], r["indice"], r["echeance"])] = float(r["valeur"])
    return out


def charger_engie():
    out = {}
    if not os.path.exists(OBS):
        return out
    for r in csv.DictReader(open(OBS, encoding="utf-8")):
        out[(r["energie"], r["indice"], r["mois"])] = float(r["valeur"])
    return out


def cmd_collect():
    connus = lire()
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    courant = mois_courant()
    observation = [decale(courant, -1), courant]
    nouveaux, revisions, vides = [], [], []
    for nom, spec in INDICES.items():
        for obs in observation:
            d1, d2 = bornes(obs)
            for k in range(0, HORIZON):
                echeance = decale(obs, k + 1)
                vals = fetch(spec, echeance, d1, d2)
                if not vals:
                    vides.append(f"{nom} {obs}->{echeance}")
                for j, v in sorted(vals.items()):
                    cle = (j, nom, echeance)
                    s = f"{v:.4f}"
                    if cle not in connus:
                        nouveaux.append({"jour": j, "indice": nom, "echeance": echeance,
                                         "valeur": s, "releve_le": horodatage})
                        connus[cle] = v
                    elif f"{connus[cle]:.4f}" != s:
                        revisions.append({"cle": "/".join(cle), "avant": f"{connus[cle]:.4f}", "apres": s})
                        nouveaux.append({"jour": j, "indice": nom, "echeance": echeance,
                                         "valeur": s, "releve_le": horodatage})
                        connus[cle] = v
                time.sleep(1)
    if nouveaux:
        neuf = not os.path.exists(JOURS)
        os.makedirs(DATA, exist_ok=True)
        with open(JOURS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CHAMPS)
            if neuf:
                w.writeheader()
            w.writerows(nouveaux)
    print(json.dumps({
        "mois_observation": observation,
        "cotations_ajoutees": len(nouveaux) - len(revisions),
        "revisions": revisions,
        "series_vides": vides,
        "total_en_base": len(connus),
    }, ensure_ascii=False, indent=2))
    return 0


def moyenne(connus, nom, echeance, obs):
    v = [x for (j, i, e), x in connus.items() if i == nom and e == echeance and j.startswith(obs)]
    return (statistics.fmean(v), len(v)) if v else (None, 0)


def cmd_nowcast():
    connus = lire()
    if not connus:
        print("base vide, lancer terme.py collect d'abord")
        return 1
    engie = charger_engie()
    obs = mois_courant()
    sorties = []
    for nom, spec in INDICES.items():
        ref = engie.get((spec["engie"][0], spec["engie"][1], obs))
        horizons = []
        for k in range(1, HORIZON + 1):
            echeance = decale(obs, k)
            m, n = moyenne(connus, nom, echeance, obs)
            if m is None:
                continue
            jours = sorted({j for (j, i, e) in connus if i == nom and e == echeance and j.startswith(obs)})
            derniere = connus[(jours[-1], nom, echeance)] if jours else None
            h = {
                "echeance": echeance,
                "moyenne_en_formation": round(m, 2),
                "cotations": n,
                "derniere_cotation": round(derniere, 2) if derniere else None,
            }
            if ref:
                h["variation_vs_mois_courant"] = f"{(m / ref - 1) * 100:+.1f}%"
            horizons.append(h)
        sorties.append({
            "indice": nom,
            "valeur_publiee_mois_courant": ref,
            "arrete_au": max((j for (j, i, e) in connus if i == nom and j.startswith(obs)), default=None),
            "horizons": horizons,
        })
    print(json.dumps({
        "mois_observation": obs,
        "source": "EEX futures, api.eex-group.com/pub/market-data",
        "avertissement": "estimation en formation, l'indice est la moyenne des cotations du mois d'observation complet",
        "indices": sorties,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_backtest():
    connus = lire()
    engie = charger_engie()
    if not connus:
        print("base vide, lancer terme.py collect d'abord")
        return 1
    lignes = []
    obs_dispo = sorted({j[:7] for (j, i, e) in connus})
    for nom, spec in INDICES.items():
        for obs in obs_dispo:
            echeance = decale(obs, 1)
            ref = engie.get((spec["engie"][0], spec["engie"][1], echeance))
            if ref is None:
                continue
            m, n = moyenne(connus, nom, echeance, obs)
            if m is None:
                continue
            an, mo = (int(x) for x in obs.split("-"))
            ouvres = sum(1 for d in range(1, calendar.monthrange(an, mo)[1] + 1)
                         if date(an, mo, d).weekday() < 5)
            lignes.append({
                "indice": nom, "observation": obs, "livraison": echeance,
                "reconstitue": round(m, 3), "engie": ref,
                "ecart_pct": round((m - ref) / ref * 100, 2),
                "cotations": n, "jours_ouvres": ouvres,
                "complet": n >= ouvres - 1,
            })
    complets = [l for l in lignes if l["complet"]]
    print(json.dumps({
        "comparaisons": len(lignes),
        "dont_mois_complets": len(complets),
        "erreur_moyenne_pct": round(statistics.fmean(abs(l["ecart_pct"]) for l in complets), 3) if complets else None,
        "detail": lignes,
    }, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"collect": cmd_collect, "nowcast": cmd_nowcast, "backtest": cmd_backtest}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "nowcast"
    if nom not in COMMANDES:
        print(f"usage: terme.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
