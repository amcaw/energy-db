import json
import os
import sys
from collections import Counter

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RACINE, "data")
BORNES = {"electricite": (0.5, 60.0), "gaz": (0.2, 30.0)}


def charger(nom):
    chemin = os.path.join(DATA, nom)
    if not os.path.exists(chemin):
        sys.exit(f"absent : {nom}")
    return json.load(open(chemin, encoding="utf-8"))


def valeur(indices, serie, mois):
    s = indices.get(serie)
    if not s:
        return None
    if mois in s["publie"]:
        return s["publie"][mois]
    e = s.get("estime")
    return e["valeur"] if e and e["mois"] == mois else None


def main():
    indices = charger("indices.json")["indices"]
    paquet = charger("contrats.json")
    contrats = paquet["contrats"]
    gaz = charger("contrats_gaz.json")["contrats"]
    ennuis = Counter()
    details = []

    def noter(quoi, detail):
        ennuis[quoi] += 1
        if len(details) < 12:
            details.append(f"{quoi} : {detail}")

    doublons = [c for c, n in Counter(c["cle"] for c in contrats).items() if n > 1]
    for c in doublons:
        noter("cle en double", c)

    if len(gaz) != sum(1 for c in contrats if c["energie"] == "gaz"):
        noter("contrats_gaz desynchronise", f"{len(gaz)} contre le filtre sur contrats.json")

    dernier = {}
    for nom, s in indices.items():
        dernier[nom] = s.get("estime", {}).get("mois") or s["dernier_mois"]
        if not s["publie"]:
            noter("indice sans valeur", nom)

    for c in contrats:
        if not c["lisible"]:
            if not c.get("raison_non_lue"):
                noter("contrat non lu sans motif", c["cle"])
            continue
        conso = [f for f in c["formules"] if f["flux"] == "consommation"]
        if not conso:
            noter("contrat lisible sans formule de consommation", c["cle"])
            continue
        for f in c["formules"]:
            if f["serie"] not in indices:
                noter("serie inconnue", f"{c['cle']} -> {f['serie']!r}")
                continue
            mois = dernier[f["serie"]]
            v = valeur(indices, f["serie"], mois)
            if v is None:
                noter("pas de valeur d indice", f"{c['cle']} {f['serie']} {mois}")
                continue
            if f["flux"] != "consommation":
                continue
            p = (f["a"] * v * f["rapport"] + f["b"]) * (1 + f["tva_pct"] / 100)
            bas, haut = BORNES.get(c["energie"], (0.2, 60.0))
            if not bas <= p <= haut:
                noter("prix invraisemblable",
                      f"{c['cle']} {f['usage']} {mois} -> {p:.2f} c/kWh")

    print(json.dumps({
        "contrats": len(contrats),
        "lisibles": sum(1 for c in contrats if c["lisible"]),
        "formules": sum(len(c["formules"]) for c in contrats),
        "indices": len(indices),
        "ennuis": dict(ennuis),
        "detail": details,
    }, ensure_ascii=False, indent=1))
    return 1 if ennuis else 0


if __name__ == "__main__":
    sys.exit(main())
