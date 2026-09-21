import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BE = ZoneInfo("Europe/Brussels")
API = "https://api.energy-charts.info/price"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
JOURS = os.path.join(DATA, "epex_jours.csv")
OBS = os.path.join(DATA, "observations.csv")


def clos(fin):
    return date.fromisoformat(fin) < datetime.now(BE).date()


def fetch(debut, fin, essais=6):
    cle = os.path.join(CACHE, f"epex-{debut}-{fin}.json")
    if os.path.exists(cle) and clos(fin):
        with open(cle, encoding="utf-8") as f:
            return json.load(f)
    url = f"{API}?bzn=BE&start={debut}&end={fin}"
    attente = 5
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "barometre-energie/1.0 (redaction)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8"))
            if clos(fin):
                os.makedirs(CACHE, exist_ok=True)
                with open(cle, "w", encoding="utf-8") as f:
                    json.dump(d, f)
            return d
        except urllib.error.HTTPError as e:
            if e.code != 429 or n == essais - 1:
                raise
            time.sleep(attente)
            attente *= 2
    raise RuntimeError("inatteignable")


def moyennes_journalieres(debut, fin):
    d = fetch(debut, fin)
    par_jour = defaultdict(list)
    for t, p in zip(d["unix_seconds"], d["price"]):
        if p is None:
            continue
        lt = datetime.fromtimestamp(t, BE)
        par_jour[lt.date()].append(p)
    return {j: statistics.fmean(v) for j, v in sorted(par_jour.items())}, len(d["price"])


def indice_mensuel(jours, mois):
    vals = [v for j, v in jours.items() if f"{j.year}-{j.month:02d}" == mois]
    if not vals:
        return None
    return statistics.fmean(vals)


def jours_du_mois(mois):
    an, mo = (int(x) for x in mois.split("-"))
    fin = date(an + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    return date(an, mo, 1), fin


def charger_engie():
    out = {}
    if not os.path.exists(OBS):
        return out
    for r in csv.DictReader(open(OBS, encoding="utf-8")):
        if r["energie"] == "electricite" and r["indice"] == "Epex DAM":
            out[r["mois"]] = float(r["valeur"])
    return out


def cmd_backtest():
    engie = charger_engie()
    if not engie:
        print("aucune valeur Engie, lancer engie.py d'abord")
        return 1
    annees = sorted({m[:4] for m in engie})
    jours = {}
    for a in annees:
        j, _ = moyennes_journalieres(f"{a}-01-01", f"{a}-12-31")
        jours.update(j)
        time.sleep(2)
    exact, ecarts, absents = 0, [], []
    for mois in sorted(engie):
        calc = indice_mensuel(jours, mois)
        if calc is None:
            absents.append(mois)
            continue
        delta = round(calc, 2) - engie[mois]
        if abs(delta) <= 0.011:
            exact += 1
        else:
            ecarts.append({"mois": mois, "calcule": round(calc, 4),
                           "engie": engie[mois], "ecart": round(delta, 4)})
    testes = len(engie) - len(absents)
    print(json.dumps({
        "mois_compares": testes,
        "concordance_exacte": exact,
        "taux": f"{exact / testes * 100:.1f}%" if testes else "n/a",
        "ecarts": ecarts,
        "sans_donnee_api": absents,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_collect():
    aujourd = datetime.now(BE).date()
    mois = f"{aujourd.year}-{aujourd.month:02d}"
    debut, fin = jours_du_mois(mois)
    jours, _ = moyennes_journalieres(debut.isoformat(), fin.isoformat())
    connus = {j: v for j, v in jours.items() if j <= aujourd + timedelta(days=1)}
    os.makedirs(DATA, exist_ok=True)
    existants = {}
    if os.path.exists(JOURS):
        for r in csv.DictReader(open(JOURS, encoding="utf-8")):
            existants[r["jour"]] = r["moyenne"]
    nouveaux = []
    for j, v in sorted(connus.items()):
        s = f"{v:.4f}"
        if existants.get(j.isoformat()) != s:
            nouveaux.append({"jour": j.isoformat(), "moyenne": s,
                             "releve_le": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if nouveaux:
        neuf = not os.path.exists(JOURS)
        with open(JOURS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["jour", "moyenne", "releve_le"])
            if neuf:
                w.writeheader()
            w.writerows(nouveaux)
    print(json.dumps({"mois": mois, "jours_ajoutes": len(nouveaux)}, ensure_ascii=False))
    return 0


def cmd_historique():
    aujourd = datetime.now(BE).date()
    debut = int(sys.argv[2]) if len(sys.argv) > 2 else 2020
    existants = {}
    if os.path.exists(JOURS):
        for r in csv.DictReader(open(JOURS, encoding="utf-8")):
            existants[r["jour"]] = r["moyenne"]
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nouveaux, annees = [], []
    for an in range(debut, aujourd.year + 1):
        jours, _ = moyennes_journalieres(f"{an}-01-01", f"{an}-12-31")
        ajoutes = 0
        for j, v in sorted(jours.items()):
            if j > aujourd:
                continue
            valeur = f"{v:.4f}"
            if existants.get(j.isoformat()) != valeur:
                nouveaux.append({"jour": j.isoformat(), "moyenne": valeur,
                                 "releve_le": horodatage})
                existants[j.isoformat()] = valeur
                ajoutes += 1
        annees.append({"annee": an, "jours_ajoutes": ajoutes})
        time.sleep(2)
    if nouveaux:
        neuf = not os.path.exists(JOURS)
        os.makedirs(DATA, exist_ok=True)
        with open(JOURS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["jour", "moyenne", "releve_le"])
            if neuf:
                w.writeheader()
            w.writerows(nouveaux)
    print(json.dumps({"depuis": debut, "jours_ajoutes": len(nouveaux),
                      "total_en_base": len(existants), "par_annee": annees},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_nowcast():
    aujourd = datetime.now(BE).date()
    mois = f"{aujourd.year}-{aujourd.month:02d}"
    debut, fin = jours_du_mois(mois)
    jours, _ = moyennes_journalieres(debut.isoformat(), fin.isoformat())
    connus = sorted(jours.items())
    if not connus:
        print(json.dumps({"erreur": "aucune donnee pour le mois"}))
        return 1

    acquis = [v for _, v in connus]
    n_total = fin.day
    estim = statistics.fmean(acquis)
    engie = charger_engie()
    precedent = engie.get(f"{debut.year if debut.month > 1 else debut.year - 1}-"
                          f"{(debut.month - 1) or 12:02d}")

    res = {
        "mois": mois,
        "arrete_au": connus[-1][0].isoformat(),
        "jours_connus": len(acquis),
        "jours_total": n_total,
        "part_du_mois": f"{len(acquis) / n_total * 100:.0f}%",
        "estimation_fin_de_mois": round(estim, 2),
        "unite": "EUR/MWh",
        "methode": "moyenne des moyennes journalieres des jours connus, "
                   "identique au calcul publie par le site",
        "source": "energy-charts.info (Fraunhofer ISE), day-ahead BE",
    }
    if precedent:
        res["mois_precedent"] = precedent
        res["variation_estimee"] = f"{(estim / precedent - 1) * 100:+.1f}%"
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"backtest": cmd_backtest, "collect": cmd_collect,
             "historique": cmd_historique, "nowcast": cmd_nowcast}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "nowcast"
    if nom not in COMMANDES:
        print(f"usage: epex.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
