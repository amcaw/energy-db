import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURS = os.path.join(RACINE, "data", "cwape", "jours")
SORTIE = os.path.join(RACINE, "data", "cwape_gaz.json")

API = "https://api.compacwape.be"
ENTETES = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://app.compacwape.be",
    "Referer": "https://app.compacwape.be/",
    "User-Agent": "energy-db/1.0 (suivi des prix du gaz)",
}
SOURCE = "Comparateur officiel de la CWaPE (compacwape.be), simulations publiques"
PERIMETRE = ("Composante énergie du fournisseur (prix du kWh et redevance fixe), hors réseau et taxes, "
             "clients résidentiels en Wallonie. Identique quel que soit le gestionnaire de réseau.")
LOCALITE = 818
LOCALITE_NOM = "1300 Wavre (Limal)"
CONSOMMATIONS = [5000, 20000]
TOLERANCE_KWH = 0.0005
TOLERANCE_REDEVANCE = 0.01
PAUSE = 0.4
DETAILS = ["en_ligne", "debut", "fin", "formule", "parametre", "conditions", "fiche", "conditions_generales", "services_payants"]


def appel(chemin, corps, essais=5):
    attente = 3
    for n in range(essais):
        requete = urllib.request.Request(API + chemin, data=json.dumps(corps).encode(),
                                         headers=ENTETES, method="POST")
        try:
            with urllib.request.urlopen(requete, timeout=120) as reponse:
                return json.loads(reponse.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as erreur:
            code = getattr(erreur, "code", None)
            if code is not None and code < 500 and code != 429:
                raise
            if n == essais - 1:
                raise
            time.sleep(attente)
            attente *= 2


def simuler(kwh):
    corps = {
        "isElectricitySimulation": False, "isGasSimulation": True,
        "consumerType": "resident", "isProsumer": False,
        "postalCode": f"/postal_codes/{LOCALITE}",
        "isGasConsumptionUnknown": False, "gasConsumption": kwh,
        "electricityConsumptionsPerCounterType": [], "prosumerConsumptionsPerCounterType": [],
        "computeNetForProsumer": False, "hasElectricitySmartCounter": False,
        "isInjectionSeparated": False, "hasCompensation": False,
        "nightOnly": False, "isTariffImpact": False,
    }
    reponse = appel("/offer_simulations", corps)
    time.sleep(PAUSE)
    return (reponse.get("gas") or {}).get("providerProduct", [])


def texte(valeur):
    return re.sub(r"\s+", " ", valeur or "").strip() or None


def en_francais(produit, champ):
    traduit = ((produit.get("translations") or {}).get("FR") or {}).get(champ)
    return texte(traduit) or texte(produit.get(champ))


def date_jour(valeur):
    return valeur[:10] if valeur else None


def lire_offres(lignes, kwh):
    offres = {}
    for ligne in lignes:
        produit = ligne["providerProduct"]
        o = offres.setdefault(produit["id"], {
            "id": produit["id"],
            "fournisseur": (produit.get("providerName") or ligne["provider"].get("name", "")).strip(),
            "produit": produit["name"].strip(),
            "type": "fixe" if produit.get("billingBase") == "fixed" else "variable",
            "duree": produit.get("contractDuration"),
            "en_ligne": bool(produit.get("isOnline")),
            "debut": date_jour(produit.get("startAt")),
            "fin": date_jour(produit.get("endAt")),
            "formule": texte(produit.get("priceFormula")),
            "parametre": texte(produit.get("indexingParameter")),
            "conditions": [c for c in (texte(x) for x in ligne.get("providerProductConditions") or []) if c],
            "fiche": en_francais(produit, "sheetPageUrl"),
            "conditions_generales": en_francais(produit, "termsAndConditionsUrl"),
            "services_payants": en_francais(produit, "additionalServiceDescription"),
            "redevance": 0.0, "prix_kwh": 0.0,
        })
        if ligne["invoiceItem"]["billingBase"] == "fixed":
            o["redevance"] = round(o["redevance"] + ligne["price"], 4)
        else:
            o["prix_kwh"] = round(o["prix_kwh"] + ligne["price"] / kwh * 100, 6)
    return sorted(offres.values(), key=lambda o: (o["fournisseur"], o["produit"], o["id"]))


def incoherences(releves):
    reference = {o["id"]: o for o in releves[-1]}
    problemes = []
    for releve in releves[:-1]:
        autre = {o["id"]: o for o in releve}
        for i, o in reference.items():
            a = autre.get(i)
            if a is None:
                problemes.append(f"{o['fournisseur']} {o['produit']} : absente d'une simulation")
            elif (abs(a["prix_kwh"] - o["prix_kwh"]) > TOLERANCE_KWH
                  or abs(a["redevance"] - o["redevance"]) > TOLERANCE_REDEVANCE):
                problemes.append(f"{o['fournisseur']} {o['produit']} : prix non linéaire")
    return problemes


def cmd_tarifs():
    releves = [lire_offres(simuler(kwh), kwh) for kwh in CONSOMMATIONS]
    problemes = incoherences(releves)
    if problemes:
        print(json.dumps({"erreur": "prix incohérents entre consommations", "offres": problemes},
                         ensure_ascii=False, indent=2))
        return 1
    offres = releves[-1]
    maintenant = datetime.now(timezone.utc)
    jour = {"date": maintenant.date().isoformat(), "releve_le": maintenant.isoformat(timespec="seconds"),
            "source": SOURCE, "localite": LOCALITE_NOM, "offres": offres}
    os.makedirs(JOURS, exist_ok=True)
    with open(os.path.join(JOURS, f"{jour['date']}.json"), "w", encoding="utf-8") as f:
        json.dump(jour, f, ensure_ascii=False, indent=1)
    construire()
    print(json.dumps({"date": jour["date"], "offres": len(offres),
                      "fixes": sum(o["type"] == "fixe" for o in offres),
                      "variables": sum(o["type"] == "variable" for o in offres)},
                     ensure_ascii=False))
    return 0


def construire():
    jours = []
    for nom in sorted(f for f in os.listdir(JOURS) if f.endswith(".json")):
        with open(os.path.join(JOURS, nom), encoding="utf-8") as f:
            jours.append(json.load(f))
    dernier_du_mois = {j["date"][:7]: j for j in jours}
    historique = {}
    for mois, j in sorted(dernier_du_mois.items()):
        for o in j["offres"]:
            h = historique.setdefault(str(o["id"]), {"mois": {}})
            h.update(fournisseur=o["fournisseur"], produit=o["produit"], type=o["type"], duree=o["duree"])
            h["mois"][mois] = {"prix_kwh": o["prix_kwh"], "redevance": o["redevance"], "releve_le": j["date"],
                               **{k: o.get(k) for k in DETAILS}}
    actuel = jours[-1]
    paquet = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "perimetre": PERIMETRE,
        "releve_le": actuel["date"],
        "unites": {"prix_kwh": "c EUR/kWh TVAC", "redevance": "EUR/an TVAC"},
        "offres_actuelles": actuel["offres"],
        "mois": sorted(dernier_du_mois),
        "historique": historique,
    }
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(paquet, f, ensure_ascii=False, separators=(",", ":"))


COMMANDES = {"tarifs": cmd_tarifs, "construire": lambda: construire() or 0}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "tarifs"
    if nom not in COMMANDES:
        print(f"usage: cwape.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
