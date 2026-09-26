import csv
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from pyxlsb import open_workbook

BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
OBS = os.path.join(DATA, "observations.csv")
ENTETE_OBS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]
ENTETES = {"User-Agent": "barometre-energie/1.0 (redaction)"}
API = "https://api.energy-charts.info/price"
BASE = "https://www.synergrid.be/images/downloads"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

INDICE_RLP = "Epex DAM RLP"
INDICE_SPP = "Epex DAM SPP"
SOURCE = "EPEX day-ahead BE (energy-charts.info) pondere par le profil Synergrid"
PREMIER_MOIS = "2025-10"
PROFILS = os.path.join(DATA, "profils")
ENCOURS = os.path.join(DATA, "indices_profil_encours.json")

SOURCES = {
    "rlp": {
        2025: ("RLP0N_2025_Electricity_all_DSOs.xlsb",
               f"{BASE}/RLP0N%202025%20Electricity%20all%20DSOs.xlsb"),
        2026: ("RLP0N_2026_Electricity_all_DSOs.xlsb",
               f"{BASE}/SLP-RLP-SPP/2026/RLP0N%202026%20Electricity%20all%20DSOs.xlsb"),
    },
    "spp": {
        2025: ("SPP_2025.xlsx",
               f"{BASE}/SLP-RLP-SPP/2025/spp-2025-ex-ante-and-ex-post_v3.0.xlsx"),
        2026: ("SPP_2026.xlsx",
               f"{BASE}/SLP-RLP-SPP/2026/SPP_ex-ante_and_ex-post_2026.xlsx"),
    },
}


def telecharger(fichier, url):
    os.makedirs(CACHE, exist_ok=True)
    chemin = os.path.join(CACHE, fichier)
    if os.path.exists(chemin) and os.path.getsize(chemin) > 100000:
        return chemin
    req = urllib.request.Request(url, headers=ENTETES)
    with urllib.request.urlopen(req, timeout=300) as r:
        corps = r.read()
    with open(chemin, "wb") as f:
        f.write(corps)
    return chemin


def colonne_de(ref):
    n = 0
    for c in ref:
        if not c.isalpha():
            break
        n = n * 26 + (ord(c.upper()) - 64)
    return n - 1


def feuille_xml(z, motif):
    wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    cibles = {r.get("Id"): r.get("Target") for r in rels}
    for sh in wb.iter(f"{NS}sheet"):
        if motif.lower() in (sh.get("name") or "").lower():
            cible = cibles[sh.get(f"{REL}id")].lstrip("/")
            return cible if cible.startswith("xl/") else "xl/" + cible
    raise SystemExit(f"feuille {motif!r} introuvable")


def lignes_xlsx(chemin, motif):
    with zipfile.ZipFile(chemin) as z:
        nom = feuille_xml(z, motif)
        with z.open(nom) as flux:
            for _, el in ElementTree.iterparse(flux, events=("end",)):
                if el.tag != f"{NS}row":
                    continue
                valeurs = {}
                for c in el:
                    v = c.find(f"{NS}v")
                    if v is None or v.text is None or c.get("t") == "s":
                        continue
                    try:
                        valeurs[colonne_de(c.get("r") or "")] = float(v.text)
                    except ValueError:
                        continue
                el.clear()
                if valeurs:
                    yield valeurs


def compact(genre, annee):
    return os.path.join(PROFILS, f"{genre}_{annee}.json")


def charger_compact(genre, annee):
    chemin = compact(genre, annee)
    if not os.path.exists(chemin):
        return None
    return json.load(open(chemin, encoding="utf-8"))


def profil_spp(annee):
    pret = charger_compact("spp", annee)
    if pret is not None:
        return pret
    fichier, url = SOURCES["spp"][annee]
    chemin = telecharger(fichier, url)
    par = defaultdict(list)
    for v in lignes_xlsx(chemin, "ex-ante"):
        if 1 not in v or 2 not in v or 3 not in v:
            continue
        cols = [v[k] for k in sorted(v) if k >= 6]
        if not cols:
            continue
        par[f"{int(v[1])}-{int(v[2]):02d}-{int(v[3]):02d}"].append(statistics.fmean(cols))
    return par


def profil_rlp(annee):
    pret = charger_compact("rlp", annee)
    if pret is not None:
        return pret
    fichier, url = SOURCES["rlp"][annee]
    chemin = telecharger(fichier, url)
    par = defaultdict(list)
    with open_workbook(chemin) as wb:
        with wb.get_sheet(wb.sheets[0]) as sh:
            for i, row in enumerate(sh.rows()):
                if i < 3:
                    continue
                v = {c.c: c.v for c in row}
                if not isinstance(v.get(2), float):
                    continue
                cols = [v[k] for k in sorted(v) if k >= 7 and isinstance(v[k], float)]
                if not cols:
                    continue
                par[f"{int(v[1])}-{int(v[2]):02d}-{int(v[3]):02d}"].append(
                    statistics.fmean(cols))
    return par


def obtenir(url, essais=6):
    attente = 5
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers=ENTETES)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == essais - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if n == essais - 1:
                raise
        time.sleep(attente)
        attente *= 2
    raise RuntimeError("inatteignable")


def cmd_extraire():
    os.makedirs(PROFILS, exist_ok=True)
    ecrits = []
    for annee in sorted(SOURCES["rlp"]):
        for genre, lecteur in (("rlp", profil_rlp), ("spp", profil_spp)):
            chemin = compact(genre, annee)
            if os.path.exists(chemin):
                continue
            par = lecteur(annee)
            compacte = {j: [round(v, 10) for v in vs] for j, vs in sorted(par.items())}
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(compacte, f, separators=(",", ":"))
            ecrits.append({"fichier": os.path.relpath(chemin, DATA),
                           "jours": len(compacte),
                           "pas": sum(len(v) for v in compacte.values()),
                           "octets": os.path.getsize(chemin)})
    print(json.dumps({"profils_ecrits": ecrits}, ensure_ascii=False, indent=2))
    return 0


def bornes(mois):
    an, mo = int(mois[:4]), int(mois[5:7])
    fin = date(an + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    return f"{mois}-01", fin.isoformat()


def prix_quart(mois):
    debut, fin = bornes(mois)
    chemin = os.path.join(CACHE, f"epex-{debut}-{fin}.json")
    clos = date.fromisoformat(fin) < datetime.now(BE).date()
    if os.path.exists(chemin) and clos:
        d = json.load(open(chemin, encoding="utf-8"))
    else:
        d = obtenir(f"{API}?bzn=BE&start={debut}&end={fin}")
        if clos:
            os.makedirs(CACHE, exist_ok=True)
            json.dump(d, open(chemin, "w"))
    par = defaultdict(list)
    for t, v in zip(d.get("unix_seconds", []), d.get("price", [])):
        if v is None:
            continue
        par[datetime.fromtimestamp(t, BE).strftime("%Y-%m-%d")].append(v)
    return par


def par_heure(valeurs):
    return [sum(valeurs[i:i + 4]) / 4 for i in range(0, len(valeurs), 4)]


def somme_par_heure(valeurs):
    return [sum(valeurs[i:i + 4]) for i in range(0, len(valeurs), 4)]


def pondere(par_prix, par_poids, mois, horaire=False):
    num = den = 0.0
    jours = 0
    for jour in sorted(par_prix):
        if not jour.startswith(mois):
            continue
        prix = par_prix[jour]
        poids = par_poids.get(jour)
        if not poids:
            return None, 0
        if len(poids) == len(prix) * 4:
            prix = [p for p in prix for _ in range(4)]
        if len(poids) != len(prix):
            return None, 0
        if horaire and len(prix) % 4 == 0:
            prix, poids = par_heure(prix), somme_par_heure(poids)
        num += sum(p * w for p, w in zip(prix, poids))
        den += sum(poids)
        jours += 1
    return (num / den if den else None), jours


def mois_disponibles():
    debut = date.fromisoformat(PREMIER_MOIS + "-01")
    fin = datetime.now(BE).date()
    out, a, m = [], debut.year, debut.month
    while (a, m) <= (fin.year, fin.month):
        out.append(f"{a}-{m:02d}")
        a, m = (a + (m == 12), m % 12 + 1)
    return out


def serie_mensuelle():
    profils, out = {}, {}
    for mois in mois_disponibles():
        an = int(mois[:4])
        if an not in profils:
            if an not in SOURCES["rlp"]:
                continue
            profils[an] = (profil_rlp(an), profil_spp(an))
        par_prix = prix_quart(mois)
        time.sleep(1)
        if not par_prix:
            continue
        rlp, n1 = pondere(par_prix, profils[an][0], mois, horaire=True)
        spp, n2 = pondere(par_prix, profils[an][1], mois)
        jours = len([j for j in par_prix if j.startswith(mois)])
        out[mois] = {"rlp": rlp, "spp": spp, "jours": jours,
                     "complet": n1 == jours and jours >= 28}
    return out


def deja_publie():
    vus = set()
    if os.path.exists(OBS):
        for r in csv.DictReader(open(OBS, encoding="utf-8")):
            vus.add((r["indice"], r["mois"]))
    return vus


def cmd_publier():
    serie = serie_mensuelle()
    vus = deja_publie()
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ajouts = []
    for mois, v in sorted(serie.items()):
        if not v["complet"]:
            continue
        for indice, cle in ((INDICE_RLP, "rlp"), (INDICE_SPP, "spp")):
            if v[cle] is None or (indice, mois) in vus:
                continue
            ajouts.append({"observed_at": horodatage, "energie": "electricite",
                           "mois": mois, "indice": indice,
                           "valeur": f"{v[cle]:.4f}", "source": SOURCE, "sha256": ""})
    if ajouts:
        neuf = not os.path.exists(OBS)
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ENTETE_OBS)
            if neuf:
                w.writeheader()
            w.writerows(ajouts)
    encours = {m: v for m, v in serie.items() if not v["complet"]}
    if encours:
        mois = max(encours)
        v = encours[mois]
        os.makedirs(DATA, exist_ok=True)
        with open(ENCOURS, "w", encoding="utf-8") as f:
            json.dump({"mois": mois, "jours_connus": v["jours"],
                       INDICE_RLP: round(v["rlp"], 4) if v["rlp"] is not None else None,
                       INDICE_SPP: round(v["spp"], 4) if v["spp"] is not None else None,
                       "calcule_le": horodatage}, f, ensure_ascii=False, indent=1)
    print(json.dumps({"mois_calcules": len(serie), "valeurs_ajoutees": len(ajouts),
                      "mois_en_cours": max(encours) if encours else None,
                      "detail": [f"{a['mois']} {a['indice']} {a['valeur']}" for a in ajouts]},
                     ensure_ascii=False, indent=2))
    return 0


REFERENCE = {
    "2025-10": (78.00, 66.21), "2025-11": (89.43, 80.13), "2025-12": (87.23, 86.63),
    "2026-01": (110.95, 114.16), "2026-02": (87.35, 74.51), "2026-03": (97.41, 53.18),
    "2026-04": (84.53, 27.95), "2026-05": (97.90, 42.37), "2026-06": (120.79, 70.89),
    "2026-07": (114.29, 62.65), "2026-08": (134.94, 79.11),
}


def cmd_backtest():
    serie = serie_mensuelle()
    lignes, e_rlp, e_spp = [], [], []
    for mois, (ref_rlp, ref_spp) in sorted(REFERENCE.items()):
        v = serie.get(mois)
        if not v or v["rlp"] is None or v["spp"] is None:
            continue
        d1 = (v["rlp"] - ref_rlp) / ref_rlp * 100
        d2 = (v["spp"] - ref_spp) / ref_spp * 100
        e_rlp.append(abs(d1))
        e_spp.append(abs(d2))
        lignes.append({"mois": mois, "rlp": round(v["rlp"], 2), "rlp_ref": ref_rlp,
                       "rlp_ecart_pct": round(d1, 3), "spp": round(v["spp"], 2),
                       "spp_ref": ref_spp, "spp_ecart_pct": round(d2, 3)})
    print(json.dumps({
        "reference": "valeurs publiees par Mega et par energyprices.be",
        "mois_compares": len(lignes),
        "rlp_ecart_moyen_pct": round(statistics.fmean(e_rlp), 3) if e_rlp else None,
        "rlp_ecart_max_pct": round(max(e_rlp), 3) if e_rlp else None,
        "spp_ecart_moyen_pct": round(statistics.fmean(e_spp), 3) if e_spp else None,
        "spp_ecart_max_pct": round(max(e_spp), 3) if e_spp else None,
        "detail": lignes,
    }, ensure_ascii=False, indent=2))
    return 0


COMMANDES = {"publier": cmd_publier, "backtest": cmd_backtest,
             "extraire": cmd_extraire}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if nom not in COMMANDES:
        print(f"usage: indices_profil.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
