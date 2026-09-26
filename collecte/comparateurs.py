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
USER_AGENT = "energy-db/1.0 (suivi des prix du gaz et de l'electricite)"
REGIONS = {
    "wallonie": {
        "nom": "Wallonie",
        "dossier": "cwape",
        "api": "https://api.compacwape.be",
        "application": "https://app.compacwape.be",
        "source": "Comparateur officiel de la CWaPE (compacwape.be), simulations publiques",
        "localite": 818,
        "localite_nom": "1300 Wavre (Limal)",
        "corps": {"gaz": {}, "electricite": {}},
    },
    "bruxelles": {
        "nom": "Bruxelles",
        "dossier": "brugel",
        "api": "https://api.brusim.be",
        "application": "https://app.brusim.be",
        "source": "Comparateur officiel de Brugel (Brusim, brusim.be), simulations publiques",
        "localite": 4,
        "localite_nom": "1000 Bruxelles",
        "corps": {"gaz": {"gasConnectionPowerSegment": "/connection_power_segments/9"},
                  "electricite": {"electricityConnectionPowerSegment": "/connection_power_segments/3"}},
    },
}
ENERGIES = {
    "gaz": {
        "cle_reponse": "gas",
        "jours": "jours",
        "sortie": "{dossier}_gaz.json",
        "consommations": [5000, 20000],
        "perimetre": ("Composante énergie du fournisseur (prix du kWh et redevance fixe), hors réseau et taxes, "
                      "clients résidentiels. Identique quel que soit le gestionnaire de réseau de la région."),
    },
    "electricite": {
        "cle_reponse": "electricity",
        "jours": os.path.join("electricite", "jours"),
        "sortie": "{dossier}_electricite.json",
        "consommations": [1750, 3500],
        "compteur": "mono-horaire, compteur classique",
        "perimetre": ("Composante énergie du fournisseur (prix du kWh, certificats verts et redevance fixe), "
                      "hors réseau et taxes, clients résidentiels, compteur mono-horaire classique. "
                      "Identique quel que soit le gestionnaire de réseau de la région."),
    },
}
TOLERANCE_KWH = 0.0005
TOLERANCE_REDEVANCE = 0.01
PAUSE = 0.4
DETAILS = ["en_ligne", "debut", "fin", "formule", "parametre", "fiche", "conditions_generales"]
DETAILS_ELECTRICITE = ["certificats_verts", "tranches"]


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


def simuler(region, energie, kwh):
    gaz = energie == "gaz"
    corps = {
        "isElectricitySimulation": not gaz, "isGasSimulation": gaz,
        "consumerType": "resident", "isProsumer": False,
        "postalCode": f"/postal_codes/{region['localite']}",
        "isGasConsumptionUnknown": False, "gasConsumption": kwh if gaz else None,
        "isElectricityConsumptionUnknown": False,
        "electricityConsumptionsPerCounterType": [] if gaz else [{"counterType": 1, "consumption": kwh}],
        "prosumerConsumptionsPerCounterType": [],
        "computeNetForProsumer": False, "hasElectricitySmartCounter": False,
        "isInjectionSeparated": False, "hasCompensation": False,
        "nightOnly": False, "isTariffImpact": False,
        **region["corps"][energie],
    }
    reponse = appel(region, "/offer_simulations", corps)
    time.sleep(PAUSE)
    return (reponse.get(ENERGIES[energie]["cle_reponse"]) or {}).get("providerProduct", [])


def texte(valeur):
    return re.sub(r"\s+", " ", valeur or "").strip() or None


def en_francais(produit, champ):
    traduit = ((produit.get("translations") or {}).get("FR") or {}).get(champ)
    return texte(traduit) or texte(produit.get(champ))


def date_jour(valeur):
    return valeur[:10] if valeur else None


def montant(prix):
    if isinstance(prix, list):
        return sum(p.get("price") or 0 for p in prix)
    return prix or 0


def lire_offres(lignes, kwh, energie):
    offres = {}
    for ligne in lignes:
        produit = ligne["providerProduct"]
        if produit.get("isForSmartCounter"):
            continue
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
            **({"certificats_verts": 0.0, "tranches": False} if energie == "electricite" else {}),
        })
        item = ligne["invoiceItem"]
        prix = montant(ligne["price"])
        if item["billingBase"] == "fixed":
            o["redevance"] = round(o["redevance"] + prix, 4)
        elif energie == "electricite" and "Green certificates" in (item.get("invoiceCategoryNestedName") or ""):
            o["certificats_verts"] = round(o["certificats_verts"] + prix / kwh * 100, 6)
        else:
            o["prix_kwh"] = round(o["prix_kwh"] + prix / kwh * 100, 6)
            if energie == "electricite" and item.get("billingType") not in (None, "unique"):
                o["tranches"] = True
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
                continue
            ecarts = [abs(a["redevance"] - o["redevance"]) > TOLERANCE_REDEVANCE,
                      abs(a.get("certificats_verts", 0) - o.get("certificats_verts", 0)) > TOLERANCE_KWH]
            if not o.get("tranches"):
                ecarts.append(abs(a["prix_kwh"] - o["prix_kwh"]) > TOLERANCE_KWH)
            if any(ecarts):
                problemes.append(f"{o['fournisseur']} {o['produit']} : prix non linéaire")
    return problemes


def jours_de(region, energie):
    return os.path.join(DATA, region["dossier"], ENERGIES[energie]["jours"])


def relever(cle, energie):
    region = REGIONS[cle]
    config = ENERGIES[energie]
    releves = [lire_offres(simuler(region, energie, kwh), kwh, energie) for kwh in config["consommations"]]
    problemes = incoherences(releves)
    if problemes:
        return {"region": cle, "energie": energie, "erreur": "prix incohérents entre consommations",
                "offres": problemes}
    offres = releves[-1]
    maintenant = datetime.now(timezone.utc)
    jour = {"date": maintenant.date().isoformat(), "releve_le": maintenant.isoformat(timespec="seconds"),
            "source": region["source"], "localite": region["localite_nom"],
            "kwh_reference": config["consommations"][-1], "offres": offres}
    os.makedirs(jours_de(region, energie), exist_ok=True)
    with open(os.path.join(jours_de(region, energie), f"{jour['date']}.json"), "w", encoding="utf-8") as f:
        json.dump(jour, f, ensure_ascii=False, indent=1)
    construire(cle, energie)
    return {"region": cle, "energie": energie, "date": jour["date"], "offres": len(offres),
            "fixes": sum(o["type"] == "fixe" for o in offres),
            "variables": sum(o["type"] == "variable" for o in offres)}


def construire(cle, energie):
    region = REGIONS[cle]
    config = ENERGIES[energie]
    dossier = jours_de(region, energie)
    if not os.path.isdir(dossier):
        return
    jours = []
    for nom in sorted(f for f in os.listdir(dossier) if f.endswith(".json")):
        with open(os.path.join(dossier, nom), encoding="utf-8") as f:
            jours.append(json.load(f))
    if not jours:
        return
    details = DETAILS + (DETAILS_ELECTRICITE if energie == "electricite" else [])
    dernier_du_mois = {j["date"][:7]: j for j in jours}
    historique = {}
    for mois, j in sorted(dernier_du_mois.items()):
        for o in j["offres"]:
            h = historique.setdefault(str(o["id"]), {"mois": {}})
            h.update(fournisseur=o["fournisseur"], produit=o["produit"], type=o["type"], duree=o["duree"])
            h["mois"][mois] = {"prix_kwh": o["prix_kwh"], "redevance": o["redevance"], "releve_le": j["date"],
                               **{k: o.get(k) for k in details}}
    actuel = jours[-1]
    unites = {"prix_kwh": "c EUR/kWh TVAC", "redevance": "EUR/an TVAC"}
    if energie == "electricite":
        unites["certificats_verts"] = "c EUR/kWh TVAC"
    paquet = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": region["nom"],
        "energie": energie,
        "source": region["source"],
        "perimetre": config["perimetre"],
        "releve_le": actuel["date"],
        "kwh_reference": actuel.get("kwh_reference", config["consommations"][-1]),
        **({"compteur": config["compteur"]} if "compteur" in config else {}),
        "unites": unites,
        "offres_actuelles": actuel["offres"],
        "mois": sorted(dernier_du_mois),
        "historique": historique,
    }
    sortie = os.path.join(DATA, config["sortie"].format(dossier=region["dossier"]))
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(paquet, f, ensure_ascii=False, separators=(",", ":"))


def cibles(argument, toutes):
    return list(toutes) if argument in (None, "toutes") else [argument]


def cmd_tarifs(region, energie):
    resultats, echec = [], False
    for cle in cibles(region, REGIONS):
        for nom in cibles(energie, ENERGIES):
            try:
                resultat = relever(cle, nom)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as erreur:
                resultat = {"region": cle, "energie": nom, "erreur": str(erreur)}
            echec = echec or "erreur" in resultat
            resultats.append(resultat)
    print(json.dumps(resultats, ensure_ascii=False))
    return 1 if echec else 0


def cmd_construire(region, energie):
    for cle in cibles(region, REGIONS):
        for nom in cibles(energie, ENERGIES):
            construire(cle, nom)
    return 0


COMMANDES = {"tarifs": cmd_tarifs, "construire": cmd_construire}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "tarifs"
    region = sys.argv[2] if len(sys.argv) > 2 else None
    energie = sys.argv[3] if len(sys.argv) > 3 else None
    if (nom not in COMMANDES or (region not in (None, "toutes") and region not in REGIONS)
            or (energie not in (None, "toutes") and energie not in ENERGIES)):
        print(f"usage: comparateurs.py [{'|'.join(COMMANDES)}] [{'|'.join(REGIONS)}|toutes] "
              f"[{'|'.join(ENERGIES)}|toutes]")
        sys.exit(2)
    sys.exit(COMMANDES[nom](region, energie))
