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


def main():
    indices = charger("indices.json")["indices"]
    cwape = charger("cwape_gaz.json")
    ennuis = Counter()
    details = []

    def noter(quoi, detail):
        ennuis[quoi] += 1
        if len(details) < 12:
            details.append(f"{quoi} : {detail}")

    for nom, s in indices.items():
        if not s["publie"]:
            noter("indice sans valeur", nom)

    offres = cwape.get("offres_actuelles", [])
    if len(offres) < 20:
        noter("trop peu d offres CWaPE", len(offres))
    for o in offres:
        if o["fournisseur"] == "Tarif social":
            continue
        if not BORNES["gaz"][0] <= o["prix_kwh"] <= BORNES["gaz"][1]:
            noter("prix CWaPE invraisemblable", f"{o['fournisseur']} {o['produit']} -> {o['prix_kwh']:.2f} c/kWh")
        if not 0 <= o["redevance"] <= 400:
            noter("redevance CWaPE invraisemblable", f"{o['fournisseur']} {o['produit']} -> {o['redevance']:.2f}")
    if not cwape.get("mois"):
        noter("historique CWaPE vide", "")

    print(json.dumps({
        "indices": len(indices),
        "offres_cwape": len(offres),
        "mois_cwape": len(cwape.get("mois", [])),
        "releve_cwape": cwape.get("releve_le"),
        "ennuis": dict(ennuis),
        "detail": details,
    }, ensure_ascii=False, indent=1))
    return 1 if ennuis else 0


if __name__ == "__main__":
    sys.exit(main())
