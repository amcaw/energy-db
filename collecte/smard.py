import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BE = ZoneInfo("Europe/Brussels")
API = "https://www.smard.de/app/chart_data"
FILTRE = 4996
REGION = "DE"
ENTETES = {"User-Agent": "barometre-energie/1.0 (redaction)",
           "Accept": "application/json"}
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
JOURS = os.path.join(DATA, "epex_jours.csv")
OBS = os.path.join(DATA, "observations.csv")
TOLERANCE = 0.05


def get(url, essais=4):
    attente = 4
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers=ENTETES)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError):
            if n == essais - 1:
                raise
            time.sleep(attente)
            attente *= 2
    raise RuntimeError("inatteignable")


def semaines(depuis_ms):
    idx = get(f"{API}/{FILTRE}/{REGION}/index_hour.json")["timestamps"]
    return [t for t in idx if t >= depuis_ms]


def quotidien(depuis):
    depuis_ms = int(datetime.combine(depuis, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000) - 7 * 86400000
    par_jour = defaultdict(list)
    for t in semaines(depuis_ms):
        d = get(f"{API}/{FILTRE}/{REGION}/{FILTRE}_{REGION}_hour_{t}.json")
        for ms, v in d.get("series", []):
            if v is None:
                continue
            par_jour[datetime.fromtimestamp(ms / 1000, BE).strftime("%Y-%m-%d")].append(v)
        time.sleep(0.15)
    return {j: statistics.fmean(v) for j, v in par_jour.items() if len(v) >= 20}


def nos_jours():
    if not os.path.exists(JOURS):
        return {}
    return {r["jour"]: float(r["moyenne"])
            for r in csv.DictReader(open(JOURS, encoding="utf-8")) if r.get("jour")}


def engie():
    out = {}
    if not os.path.exists(OBS):
        return out
    for r in csv.DictReader(open(OBS, encoding="utf-8")):
        if r["energie"] == "electricite" and r["indice"] == "Epex DAM" and len(r["mois"]) == 7:
            out[r["mois"]] = float(r["valeur"])
    return out


def cmd_controle():
    nous = nos_jours()
    if not nous:
        print("epex_jours.csv vide, lancer epex.py collect d'abord")
        return 1
    debut = date.fromisoformat(min(nous))
    eux = quotidien(debut)
    communs = sorted(set(nous) & set(eux))
    if not communs:
        print(json.dumps({"erreur": "aucun jour commun"}, ensure_ascii=False))
        return 1
    ecarts = [{"jour": j, "nous": round(nous[j], 4), "smard": round(eux[j], 4),
               "ecart": round(eux[j] - nous[j], 4)}
              for j in communs if abs(eux[j] - nous[j]) > TOLERANCE]
    manquants = sorted(set(nous) - set(eux))
    res = {
        "source": f"SMARD Bundesnetzagentur, filtre {FILTRE} (Belgique), CC BY 4.0",
        "jours_compares": len(communs),
        "ecart_moyen": round(statistics.fmean(abs(eux[j] - nous[j]) for j in communs), 4),
        "ecart_max": round(max(abs(eux[j] - nous[j]) for j in communs), 4),
        "divergences": ecarts,
        "jours_absents_chez_smard": manquants,
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if ecarts else 0


def cmd_backtest():
    officiel = engie()
    if not officiel:
        print("aucune valeur Engie, lancer engie.py d'abord")
        return 1
    eux = quotidien(date.fromisoformat(min(officiel) + "-01"))
    par_mois = defaultdict(list)
    for j, v in eux.items():
        par_mois[j[:7]].append(v)
    exact, ecarts, absents = 0, [], []
    for m in sorted(officiel):
        vals = par_mois.get(m)
        if not vals or len(vals) < 27:
            absents.append(m)
            continue
        calc = round(statistics.fmean(vals), 2)
        if abs(calc - officiel[m]) <= 0.011:
            exact += 1
        else:
            ecarts.append({"mois": m, "smard": calc, "engie": officiel[m],
                           "ecart": round(calc - officiel[m], 4)})
    testes = len(officiel) - len(absents)
    print(json.dumps({
        "mois_compares": testes,
        "concordance_exacte": exact,
        "taux": f"{exact / testes * 100:.1f}%" if testes else "n/a",
        "ecarts": ecarts,
        "sans_donnee": absents,
    }, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"controle": cmd_controle, "backtest": cmd_backtest}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "controle"
    if nom not in COMMANDES:
        print(f"usage: smard.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
