import csv
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BE = ZoneInfo("Europe/Brussels")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OBS = os.path.join(DATA, "observations.csv")
PROFILS = os.path.join(DATA, "profils")
JOURS_TTF = os.path.join(DATA, "ttf_jours.csv")
ENCOURS = os.path.join(DATA, "heren_encours.json")
ENTETE_OBS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]

PAGE_LUMINUS = "https://www.luminus.be/nl/prive/energie/indexatieparameters/"
PDF_ENECO = "https://cdn.eneco.be/downloads/nl/b2c/acq/indexatieparameters-aardgas.pdf"
INDICE_SIMPLE = "TTF DAM Heren"
INDICE_RLP = "TTF DAM RLP Heren"
SOURCE_SIMPLE = "Luminus, parametres d'indexation (TTFDAHM, ICIS Heren)"
SOURCE_RLP = "Luminus (TTFDAH RLP M) recoupe avec Eneco (TTFDAW-RLP-M), ICIS Heren"
TOLERANCE_RECOUPEMENT = 0.02
MOIS = ["Januari", "Februari", "Maart", "April", "Mei", "Juni", "Juli", "Augustus",
        "September", "Oktober", "November", "December"]
ENTETES = {"User-Agent": "Mozilla/5.0 (energy-db; suivi des indices)"}


def obtenir(url, sauts=5):
    for _ in range(sauts):
        requete = urllib.request.Request(url, headers=ENTETES)
        try:
            with urllib.request.urlopen(requete, timeout=90) as reponse:
                return reponse.read()
        except urllib.error.HTTPError as erreur:
            if erreur.code not in (307, 308) or not erreur.headers.get("Location"):
                raise
            url = urllib.parse.urljoin(url, erreur.headers["Location"])
    raise RuntimeError(f"trop de redirections pour {url}")


def texte_pdf(contenu):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(contenu)
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True,
                              text=True, check=True).stdout


def pdf_luminus():
    page = obtenir(PAGE_LUMINUS).decode("utf-8", "replace")
    lien = re.search(r'/api-next/download\?fileId=file-[0-9a-f]+-pdf', page)
    if not lien:
        raise RuntimeError("lien du PDF Luminus introuvable")
    return obtenir("https://www.luminus.be" + lien.group() + "&openFile=true")


def nombre(texte):
    return float(texte.replace(",", "."))


def lire_luminus(texte):
    lignes = texte.split("\n")
    debut = next(i for i, l in enumerate(lignes) if "TTFDAHM" in l and "RLP" in l)
    annees = None
    for l in lignes[debut:debut + 4]:
        trouvees = re.findall(r"\b20\d\d\b", l)
        if len(trouvees) >= 6:
            annees = [int(a) for a in trouvees[3:6]]
            break
    if not annees:
        raise RuntimeError("annees du tableau gaz Luminus introuvables")
    motif = re.compile(r"\b(" + "|".join(MOIS) + r")\b")
    out = {"simple": {}, "rlp": {}}
    for l in lignes[debut:debut + 25]:
        occurrences = list(motif.finditer(l))
        if len(occurrences) < 2:
            continue
        for rang, cle in enumerate(("simple", "rlp")):
            m = occurrences[rang]
            fin = occurrences[rang + 1].start() if rang + 1 < len(occurrences) else len(l)
            morceau = re.split(r"\bQ[1-4]\b", l[m.end():fin])[0]
            for annee, valeur in zip(annees, re.findall(r"\d+,\d{3}", morceau)):
                out[cle][f"{annee}-{MOIS.index(m.group(1)) + 1:02d}"] = nombre(valeur)
    return out


def lire_eneco(texte):
    lignes = texte.split("\n")
    i = next(i for i, l in enumerate(lignes) if "TTFDAW-RLP-M" in l)
    colonnes = [(m.group(), m.end()) for m in re.finditer(r"\S+", lignes[i])]
    motif = re.compile(r"^\s*(" + "|".join(MOIS) + r")\s+(20\d\d)\b")
    out = {}
    for l in lignes[i + 1:]:
        m = motif.match(l)
        if not m:
            continue
        mois = f"{m.group(2)}-{MOIS.index(m.group(1)) + 1:02d}"
        for n in re.finditer(r"\d+,\d{2,3}", l[m.end():]):
            fin = m.end() + n.end()
            if min(colonnes, key=lambda c: abs(c[1] - fin))[0] == "TTFDAW-RLP-M":
                out[mois] = nombre(n.group())
    return out


def observations():
    if not os.path.exists(OBS):
        return {}
    connus = {}
    for r in csv.DictReader(open(OBS, encoding="utf-8")):
        connus[(r["indice"], r["mois"])] = r["valeur"]
    return connus


def profil_gaz(annee):
    chemin = os.path.join(PROFILS, f"rlp_gaz_{annee}.json")
    return json.load(open(chemin, encoding="utf-8")) if os.path.exists(chemin) else {}


def approximation():
    if not os.path.exists(JOURS_TTF):
        return None
    jours = {r["jour"]: float(r["valeur"]) for r in csv.DictReader(open(JOURS_TTF, encoding="utf-8"))}
    aujourd = datetime.now(BE).date()
    mois = f"{aujourd.year}-{aujourd.month:02d}"
    connus = {j: v for j, v in jours.items() if j.startswith(mois)}
    if not connus:
        return None
    profil = profil_gaz(aujourd.year)
    poids = {j: profil[j] for j in connus if j in profil}
    rlp = (sum(connus[j] * poids[j] for j in poids) / sum(poids.values())) if len(poids) == len(connus) else None
    return {"mois": mois, "jours_connus": len(connus),
            INDICE_SIMPLE: round(statistics.fmean(connus.values()), 4),
            INDICE_RLP: round(rlp, 4) if rlp is not None else None,
            "base": "EEX EGSI TTF Day + Weekend, par jour de livraison",
            "calcule_le": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def cmd_publier():
    contenu_luminus = pdf_luminus()
    contenu_eneco = obtenir(PDF_ENECO)
    luminus = lire_luminus(texte_pdf(contenu_luminus))
    eneco = lire_eneco(texte_pdf(contenu_eneco))
    empreinte = hashlib.sha256(contenu_luminus).hexdigest()[:16]
    connus = observations()
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ajouts, desaccords = [], []
    for indice, valeurs, source in ((INDICE_SIMPLE, luminus["simple"], SOURCE_SIMPLE),
                                    (INDICE_RLP, luminus["rlp"], SOURCE_RLP)):
        for mois, valeur in sorted(valeurs.items()):
            if indice == INDICE_RLP and mois in eneco and abs(eneco[mois] - valeur) > TOLERANCE_RECOUPEMENT:
                desaccords.append({"mois": mois, "luminus": valeur, "eneco": eneco[mois]})
                continue
            texte = f"{valeur:.4f}"
            if connus.get((indice, mois)) == texte:
                continue
            ajouts.append({"observed_at": horodatage, "energie": "gaz", "mois": mois,
                           "indice": indice, "valeur": texte, "source": source,
                           "sha256": empreinte})
    if ajouts:
        neuf = not os.path.exists(OBS)
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ENTETE_OBS)
            if neuf:
                w.writeheader()
            w.writerows(ajouts)
    encours = approximation()
    if encours:
        with open(ENCOURS, "w", encoding="utf-8") as f:
            json.dump(encours, f, ensure_ascii=False, indent=1)
    print(json.dumps({"mois_luminus": len(luminus["rlp"]), "mois_eneco": len(eneco),
                      "valeurs_ajoutees": len(ajouts), "desaccords": desaccords,
                      "approximation": encours}, ensure_ascii=False, indent=2))
    return 1 if desaccords else 0


COMMANDES = {"publier": cmd_publier}

if __name__ == "__main__":
    nom = sys.argv[1] if len(sys.argv) > 1 else "publier"
    if nom not in COMMANDES:
        print(f"usage: heren.py [{'|'.join(COMMANDES)}]")
        sys.exit(2)
    sys.exit(COMMANDES[nom]())
