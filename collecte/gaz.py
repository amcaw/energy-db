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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API = "https://api.eex-group.com/pub/market-data/chart/eod"
INSTRUMENT = {
    "commodity": "NATGAS",
    "area": "ZTP",
    "product": "EGSI",
    "pricing": "I",
    "shortCode": "EEX EGSI ZTP Day",
}
BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
JOURS = os.path.join(DATA, "ztp_jours.csv")
OBS = os.path.join(DATA, "observations.csv")
CHAMPS = ["jour", "valeur", "releve_le"]


def jour_de_livraison(cotation):
    return (date.fromisoformat(str(cotation)[:10]) + timedelta(days=1)).isoformat()


def fetch(debut, fin, essais=5):
    params = dict(INSTRUMENT, startDate=debut, endDate=fin)
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
                raise
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
                out[jour_de_livraison(ligne[0])] = float(ligne[1])
    return out


def bornes(mois):
    an, mo = (int(x) for x in mois.split("-"))
    return f"{mois}-01", f"{mois}-{calendar.monthrange(an, mo)[1]:02d}"


def recule(mois, n):
    an, mo = (int(x) for x in mois.split("-"))
    mo -= n
    an += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{an}-{mo:02d}"


def mois_courant():
    a = datetime.now(BE).date()
    return f"{a.year}-{a.month:02d}"


def lire_jours():
    out = {}
    if os.path.exists(JOURS):
        for r in csv.DictReader(open(JOURS, encoding="utf-8")):
            out[r["jour"]] = float(r["valeur"])
    return out


def ecrire(nouveaux):
    if not nouveaux:
        return
    neuf = not os.path.exists(JOURS)
    os.makedirs(DATA, exist_ok=True)
    with open(JOURS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS)
        if neuf:
            w.writeheader()
        w.writerows(nouveaux)


def charger_engie():
    out = {}
    if not os.path.exists(OBS):
        return out
    for r in csv.DictReader(open(OBS, encoding="utf-8")):
        if r["energie"] == "gaz" and r["indice"] == "ZTP DAM":
            out[r["mois"]] = float(r["valeur"])
    return out


def indice(jours, mois):
    v = [x for j, x in jours.items() if j.startswith(mois)]
    return statistics.fmean(v) if v else None


INDICE_EGSI = "ZTP DAM EGSI"
SOURCE_EGSI = "EEX EGSI ZTP Day + Weekend, par jour de livraison"
ENTETE_OBS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]


def cmd_publier():
    jours = lire_jours()
    existants = set()
    if os.path.exists(OBS):
        for r in csv.DictReader(open(OBS, encoding="utf-8")):
            existants.add((r["indice"], r["mois"]))
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ajouts = []
    for m in sorted({j[:7] for j in jours}):
        an, mo = (int(x) for x in m.split("-"))
        complet = sum(1 for j in jours if j.startswith(m)) == calendar.monthrange(an, mo)[1]
        if (INDICE_EGSI, m) in existants or not complet:
            continue
        ajouts.append({"observed_at": horodatage, "energie": "gaz", "mois": m,
                       "indice": INDICE_EGSI, "valeur": f"{indice(jours, m):.4f}",
                       "source": SOURCE_EGSI, "sha256": ""})
    if ajouts:
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=ENTETE_OBS).writerows(ajouts)
    print(json.dumps({"mois_publies": len(ajouts),
                      "premier": ajouts[0]["mois"] if ajouts else None,
                      "dernier": ajouts[-1]["mois"] if ajouts else None}, ensure_ascii=False))
    return 0


def cmd_collect():
    connus = lire_jours()
    courant = mois_courant()
    cibles = [courant] if connus else [recule(courant, n) for n in range(13, -1, -1)]
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nouveaux, revisions = [], []
    for mois in cibles:
        d1, d2 = bornes(mois)
        try:
            vals = fetch(d1, d2)
        except Exception as e:
            print(json.dumps({"erreur": f"{mois}: {e}"}, ensure_ascii=False))
            return 1
        for j, v in sorted(vals.items()):
            s = f"{v:.4f}"
            if j not in connus:
                nouveaux.append({"jour": j, "valeur": s, "releve_le": horodatage})
                connus[j] = v
            elif f"{connus[j]:.4f}" != s:
                revisions.append({"jour": j, "avant": f"{connus[j]:.4f}", "apres": s})
                nouveaux.append({"jour": j, "valeur": s, "releve_le": horodatage})
                connus[j] = v
        time.sleep(1)
    ecrire(nouveaux)
    print(json.dumps({
        "mois_interroges": len(cibles),
        "jours_ajoutes": len(nouveaux) - len(revisions),
        "revisions": revisions,
        "total_en_base": len(connus),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_backtest():
    engie = charger_engie()
    jours = lire_jours()
    if not jours:
        print("base vide, lancer gaz.py collect d'abord")
        return 1
    lignes, ecarts = [], []
    for mois in sorted(engie):
        calc = indice(jours, mois)
        if calc is None:
            continue
        n = sum(1 for j in jours if j.startswith(mois))
        attendu = calendar.monthrange(int(mois[:4]), int(mois[5:]))[1]
        if n < attendu - 1:
            continue
        ec = (calc - engie[mois]) / engie[mois] * 100
        ecarts.append(abs(ec))
        lignes.append({"mois": mois, "calcule": round(calc, 3),
                       "engie": engie[mois], "ecart_pct": round(ec, 2), "jours": n})
    print(json.dumps({
        "mois_compares": len(lignes),
        "erreur_moyenne_pct": round(statistics.fmean(ecarts), 3) if ecarts else None,
        "erreur_max_pct": round(max(ecarts), 3) if ecarts else None,
        "detail": lignes,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_nowcast():
    jours = lire_jours()
    if not jours:
        print("base vide, lancer gaz.py collect d'abord")
        return 1
    mois = mois_courant()
    acquis = [v for j, v in sorted(jours.items()) if j.startswith(mois)]
    if not acquis:
        print(json.dumps({"erreur": "aucune donnee pour le mois en cours"}, ensure_ascii=False))
        return 1
    an, mo = (int(x) for x in mois.split("-"))
    n_total = calendar.monthrange(an, mo)[1]
    estim = statistics.fmean(acquis)
    res = {
        "mois": mois,
        "arrete_au": max(j for j in jours if j.startswith(mois)),
        "jours_connus": len(acquis),
        "jours_total": n_total,
        "part_du_mois": f"{len(acquis) / n_total * 100:.0f}%",
        "estimation_fin_de_mois": round(estim, 2),
        "unite": "EUR/MWh",
        "indice_vise": "ZTP DAM",
        "methode": "moyenne des cotations journalieres connues, "
                   "identique au calcul publie par le site",
        "source": "EEX EGSI ZTP Day, api.eex-group.com/pub/market-data",
    }
    prec = charger_engie().get(recule(mois, 1))
    if prec:
        res["mois_precedent"] = prec
        res["variation_estimee"] = f"{(estim / prec - 1) * 100:+.1f}%"
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"collect": cmd_collect, "publier": cmd_publier, "backtest": cmd_backtest,
             "nowcast": cmd_nowcast}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "nowcast"
    if nom not in COMMANDES:
        print(f"usage: gaz.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
