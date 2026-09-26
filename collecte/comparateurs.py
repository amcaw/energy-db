import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RACINE, "data")
USER_AGENT = "energy-db/1.0 (suivi des prix du gaz)"
PERIMETRE = ("Composante énergie du fournisseur (prix du kWh et redevance fixe), hors réseau et taxes, "
             "clients résidentiels. Identique quel que soit le gestionnaire de réseau de la région.")
REGIONS = {
    "wallonie": {
        "nom": "Wallonie",
        "dossier": "cwape",
        "sortie": "cwape_gaz.json",
        "api": "https://api.compacwape.be",
        "application": "https://app.compacwape.be",
        "source": "Comparateur officiel de la CWaPE (compacwape.be), simulations publiques",
        "localite": 818,
        "localite_nom": "1300 Wavre (Limal)",
        "corps": {},
    },
    "bruxelles": {
        "nom": "Bruxelles",
        "dossier": "brugel",
        "sortie": "brugel_gaz.json",
        "api": "https://api.brusim.be",
        "application": "https://app.brusim.be",
        "source": "Comparateur officiel de Brugel (Brusim, brusim.be), simulations publiques",
        "localite": 4,
        "localite_nom": "1000 Bruxelles",
        "corps": {"gasConnectionPowerSegment": "/connection_power_segments/9"},
    },
}
CONSOMMATIONS = [5000, 20000]
TOLERANCE_KWH = 0.0005
TOLERANCE_REDEVANCE = 0.01
PAUSE = 0.4
DETAILS = ["en_ligne", "debut", "fin", "formule", "parametre", "fiche", "conditions_generales"]


def appel(region, chemin, corps, essais=5):
    entetes = {"Accept": "application/json", "Content-Type": "application/json",
               "Origin": region["application"], "Referer": region["application"] + "/",
               "User-Agent": USER_AGENT}
    attente = 3
    for n in range(essais):
        requete = urllib.request.Request(region["api"] + chemin, data=json.dumps(corps).encode(),
                                         headers=entetes, method="POST")
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


def simuler(region, kwh):
    corps = {
        "isElectricitySimulation": False, "isGasSimulation": True,
        "consumerType": "resident", "isProsumer": False,
        "postalCode": f"/postal_codes/{region['localite']}",
        "isGasConsumptionUnknown": False, "gasConsumption": kwh,
        "electricityConsumptionsPerCounterType": [], "prosumerConsumptionsPerCounterType": [],
        "computeNetForProsumer": False, "hasElectricitySmartCounter": False,
        "isInjectionSeparated": False, "hasCompensation": False,
        "nightOnly": False, "isTariffImpact": False,
        **region["corps"],
    }
    reponse = appel(region, "/offer_simulations", corps)
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
            "fiche": en_francais(produit, "sheetPageUrl"),
            "conditions_generales": en_francais(produit, "termsAndConditionsUrl"),
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


def jours_de(region):
    return os.path.join(DATA, region["dossier"], "jours")


def relever(cle):
    region = REGIONS[cle]
    releves = [lire_offres(simuler(region, kwh), kwh) for kwh in CONSOMMATIONS]
    problemes = incoherences(releves)
    if problemes:
        return {"region": cle, "erreur": "prix incohérents entre consommations", "offres": problemes}
    offres = releves[-1]
    maintenant = datetime.now(timezone.utc)
    jour = {"date": maintenant.date().isoformat(), "releve_le": maintenant.isoformat(timespec="seconds"),
            "source": region["source"], "localite": region["localite_nom"], "offres": offres}
    os.makedirs(jours_de(region), exist_ok=True)
    with open(os.path.join(jours_de(region), f"{jour['date']}.json"), "w", encoding="utf-8") as f:
        json.dump(jour, f, ensure_ascii=False, indent=1)
    construire(cle)
    return {"region": cle, "date": jour["date"], "offres": len(offres),
            "fixes": sum(o["type"] == "fixe" for o in offres),
            "variables": sum(o["type"] == "variable" for o in offres)}


def construire(cle):
    region = REGIONS[cle]
    jours = []
    for nom in sorted(f for f in os.listdir(jours_de(region)) if f.endswith(".json")):
        with open(os.path.join(jours_de(region), nom), encoding="utf-8") as f:
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
        "region": region["nom"],
        "source": region["source"],
        "perimetre": PERIMETRE,
        "releve_le": actuel["date"],
        "unites": {"prix_kwh": "c EUR/kWh TVAC", "redevance": "EUR/an TVAC"},
        "offres_actuelles": actuel["offres"],
        "mois": sorted(dernier_du_mois),
        "historique": historique,
    }
    with open(os.path.join(DATA, region["sortie"]), "w", encoding="utf-8") as f:
        json.dump(paquet, f, ensure_ascii=False, separators=(",", ":"))


def cibles(argument):
    return list(REGIONS) if argument in (None, "toutes") else [argument]


def cmd_tarifs(argument):
    resultats, echec = [], False
    for cle in cibles(argument):
        try:
            resultat = relever(cle)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as erreur:
            resultat = {"region": cle, "erreur": str(erreur)}
        echec = echec or "erreur" in resultat
        resultats.append(resultat)
    print(json.dumps(resultats, ensure_ascii=False))
    return 1 if echec else 0


def cmd_construire(argument):
    for cle in cibles(argument):
        construire(cle)
    return 0


COMMANDES = {"tarifs": cmd_tarifs, "construire": cmd_construire}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "tarifs"
    argument = sys.argv[2] if len(sys.argv) > 2 else None
    if nom not in COMMANDES or (argument not in (None, "toutes") and argument not in REGIONS):
        print(f"usage: comparateurs.py [{'|'.join(COMMANDES)}] [{'|'.join(REGIONS)}|toutes]")
        sys.exit(2)
    sys.exit(COMMANDES[nom](argument))
