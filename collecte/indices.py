import collections
import csv
import math
import os
import re
import statistics

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PONDERES = os.path.join(RACINE, "collecte", "data", "indices_ponderes.csv")

SERIE_REPERE = {"electricite": "Epex DAM", "gaz": "ZTP DAM"}
COMPTANT = {"Epex DAM", "ZTP DAM", "TTF DAM"}
PROFIL = re.compile(r"(?:^|[\s_-])(RLP|SPP)(?:$|[\s_-])", re.I)


def serie_de(nom):
    b = (nom or "").lower()
    if "endex" in b:
        return "Endex 101"
    if "ztp" in b:
        return "ZTP 101" if "101" in b else "ZTP DAM"
    if "ttf" in b:
        return "TTF 101 Heren" if "101" in b else "TTF DAM"
    if any(k in b for k in ("epex", "belpex", "belix")):
        return "Epex DAM"
    return None


def famille_de(nom):
    b = (nom or "").lower()
    for cle in ("endex", "ztp", "ttf", "zig", "epex", "belpex", "belix"):
        if cle in b:
            return "spot_elec" if cle in ("epex", "belpex", "belix") else cle
    return "?"


def profil_de(nom):
    m = PROFIL.search(nom or "")
    return m.group(1).upper() if m else "base"


def cle_rapport(nom):
    return (serie_de(nom), profil_de(nom))


SERIES_PROFIL = {
    ("Epex DAM", "RLP"): "Epex DAM RLP",
    ("Epex DAM", "SPP"): "Epex DAM SPP",
}


def serie_profil(nom, disponibles=()):
    serie = SERIES_PROFIL.get(cle_rapport(nom))
    return serie if serie in disponibles else None


def serie_indice(nom, disponibles=()):
    return serie_profil(nom, disponibles) or serie_de(nom)


def rapports(chemin=PONDERES):
    par = collections.defaultdict(list)
    if not os.path.exists(chemin):
        return {}
    for r in csv.DictReader(open(chemin, encoding="utf-8")):
        try:
            v = float(r["rapport"])
        except (ValueError, KeyError, TypeError):
            continue
        if not 0.5 < v < 2.0:
            continue
        cle = cle_rapport(r["indice"])
        if cle[0] is None:
            continue
        par[cle].append(v)
    out = {}
    for cle, vals in par.items():
        if len(vals) < 3:
            continue
        median = statistics.median(vals)
        erreur = statistics.pstdev(vals) / math.sqrt(len(vals))
        if abs(median - 1) <= erreur:
            continue
        out[cle] = round(median, 4)
    return out


def rapport_de(ratios, nom, disponibles=()):
    if serie_profil(nom, disponibles):
        return 1.0
    return ratios.get(cle_rapport(nom), 1.0)
