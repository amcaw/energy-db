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
INSTRUMENTS = [
    {"commodity": "NATGAS", "area": "TTF", "product": "EGSI", "pricing": "I",
     "shortCode": "EEX EGSI TTF Day"},
    {"commodity": "NATGAS", "area": "TTF", "product": "EGSI", "pricing": "I",
     "shortCode": "EEX EGSI TTF Weekend"},
]
BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
JOURS = os.path.join(DATA, "ttf_jours.csv")
OBS = os.path.join(DATA, "observations.csv")
CHAMPS = ["jour", "valeur", "releve_le"]
ENTETE_OBS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]
INDICE = "TTF DAM"
SOURCE = "EEX EGSI TTF Day + Weekend, api.eex-group.com/pub/market-data"
ENTETES = {
    "Accept": "application/json",
    "Referer": "https://eds.eex-group.com/",
    "User-Agent": "barometre-energie/1.0 (redaction)",
}


def fetch(spec, debut, fin, essais=5):
    params = dict(spec, startDate=debut, endDate=fin)
    url = API + "?" + urllib.parse.urlencode(params)
    attente = 4
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers=ENTETES)
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
                out[ligne[0]] = float(ligne[1])
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


def indice(jours, mois):
    v = [x for j, x in jours.items() if j.startswith(mois)]
    return statistics.fmean(v) if v else None


def mois_complet(jours, mois):
    n = sum(1 for j in jours if j.startswith(mois))
    return n >= calendar.monthrange(int(mois[:4]), int(mois[5:7]))[1] - 1


def cmd_collect():
    connus = lire_jours()
    courant = mois_courant()
    profondeur = int(sys.argv[2]) if len(sys.argv) > 2 else (0 if connus else 24)
    cibles = [recule(courant, n) for n in range(profondeur, -1, -1)]
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nouveaux, revisions = [], []
    for mois in cibles:
        d1, d2 = bornes(mois)
        vals = {}
        for spec in INSTRUMENTS:
            try:
                vals.update(fetch(spec, d1, d2))
            except Exception as e:
                print(json.dumps({"erreur": f"{mois} {spec['shortCode']}: {e}"},
                                 ensure_ascii=False))
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
        "revisions": len(revisions),
        "total_en_base": len(connus),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_publier():
    jours = lire_jours()
    if not jours:
        print("base vide, lancer ttf.py collect d'abord")
        return 1
    mois = sorted({j[:7] for j in jours})
    existants = set()
    if os.path.exists(OBS):
        for r in csv.DictReader(open(OBS, encoding="utf-8")):
            existants.add((r["indice"], r["mois"]))
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ajouts = []
    for m in mois:
        if (INDICE, m) in existants or not mois_complet(jours, m):
            continue
        ajouts.append({"observed_at": horodatage, "energie": "gaz", "mois": m,
                       "indice": INDICE, "valeur": f"{indice(jours, m):.4f}",
                       "source": SOURCE, "sha256": ""})
    if ajouts:
        neuf = not os.path.exists(OBS)
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ENTETE_OBS)
            if neuf:
                w.writeheader()
            w.writerows(ajouts)
    print(json.dumps({"mois_publies": len(ajouts),
                      "premier": ajouts[0]["mois"] if ajouts else None,
                      "dernier": ajouts[-1]["mois"] if ajouts else None},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_comparer():
    jours = lire_jours()
    ref = {}
    if os.path.exists(OBS):
        for r in csv.DictReader(open(OBS, encoding="utf-8")):
            if r["indice"] == "TTF 101 Heren" and len(r["mois"]) == 7:
                ref[r["mois"]] = float(r["valeur"])
    lignes, ecarts = [], []
    for m in sorted(ref):
        v = indice(jours, m)
        if v is None or not mois_complet(jours, m):
            continue
        e = (v - ref[m]) / ref[m] * 100
        ecarts.append(abs(e))
        lignes.append({"mois": m, "spot": round(v, 2), "terme_101": ref[m],
                       "ecart_pct": round(e, 2)})
    print(json.dumps({
        "mois_compares": len(lignes),
        "ecart_moyen_pct": round(statistics.fmean(ecarts), 2) if ecarts else None,
        "ecart_max_pct": round(max(ecarts), 2) if ecarts else None,
        "detail": lignes[-14:],
    }, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"collect": cmd_collect, "publier": cmd_publier, "comparer": cmd_comparer}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "comparer"
    if nom not in COMMANDES:
        print(f"usage: ttf.py [{'|'.join(COMMANDES)}] [profondeur]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
