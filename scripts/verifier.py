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
    comparateurs = {"CWaPE gaz": (charger("cwape_gaz.json"), 20), "Brugel gaz": (charger("brugel_gaz.json"), 8),
                    "CWaPE électricité": (charger("cwape_electricite.json"), 20),
                    "Brugel électricité": (charger("brugel_electricite.json"), 8)}
    ennuis = Counter()
    details = []

    def noter(quoi, detail):
        ennuis[quoi] += 1
        if len(details) < 12:
            details.append(f"{quoi} : {detail}")

    for nom, s in indices.items():
        if not s["publie"]:
            noter("indice sans valeur", nom)

    for nom, (paquet, minimum) in comparateurs.items():
        offres = paquet.get("offres_actuelles", [])
        if len(offres) < minimum:
            noter(f"trop peu d offres {nom}", len(offres))
        for o in offres:
            if o["fournisseur"].lower() == "tarif social":
                continue
            bornes = BORNES["electricite" if "électricité" in nom else "gaz"]
            if not bornes[0] <= o["prix_kwh"] <= bornes[1]:
                noter(f"prix {nom} invraisemblable", f"{o['fournisseur']} {o['produit']} -> {o['prix_kwh']:.2f} c/kWh")
            if not 0 <= o["redevance"] <= 400:
                noter(f"redevance {nom} invraisemblable", f"{o['fournisseur']} {o['produit']} -> {o['redevance']:.2f}")
        if not paquet.get("mois"):
            noter(f"historique {nom} vide", "")

    print(json.dumps({
        "indices": len(indices),
        "comparateurs": {nom: {"offres": len(p.get("offres_actuelles", [])), "mois": len(p.get("mois", [])),
                               "releve": p.get("releve_le")} for nom, (p, _) in comparateurs.items()},
        "ennuis": dict(ennuis),
        "detail": details,
    }, ensure_ascii=False, indent=1))
    return 1 if ennuis else 0


if __name__ == "__main__":
    sys.exit(main())
