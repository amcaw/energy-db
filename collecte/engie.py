import csv
import difflib
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timezone

BASE = "https://www.engie.be/fr/energie/electricite-gaz/prix-conditions/parametres-indexation"
PAGES = {
    "electricite": f"{BASE}/parametres-indexation-electricite/",
    "gaz": f"{BASE}/parametres-indexation-gaz/",
}
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SNAP = os.path.join(DATA, "snapshots")
OBS = os.path.join(DATA, "observations.csv")
FIELDS = ["observed_at", "energie", "mois", "indice", "valeur", "source", "sha256"]

MOIS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
TRIM = re.compile(r"(\d)(?:er|ème|eme)\s+trimestre\s+de\s+(\d{4})", re.I)
ACCENTS = str.maketrans("àâäéèêëîïôöùûü", "aaaeeeeiioouuu")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "barometre-energie/1.0 (redaction)",
        "Accept-Language": "fr-BE,fr;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def strip(cell):
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()


def parse_table(page):
    start = page.find("<table")
    end = page.find("</table>", start)
    if start < 0 or end < 0:
        raise ValueError("tableau introuvable")
    table = page[start:end]
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", table, re.S):
        rows.append([strip(c) for c in re.findall(r"<t[hd].*?</t[hd]>", tr, re.S)])
    return rows


def normalise_periode(label):
    low = label.lower().strip().translate(ACCENTS)
    m = TRIM.search(low)
    if m:
        return f"{m.group(2)}-T{m.group(1)}"
    parts = low.replace("\u2019", " ").split()
    if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) != 4:
        return None
    nom = parts[0]
    if nom in MOIS:
        return f"{parts[1]}-{MOIS[nom]:02d}"
    proche = difflib.get_close_matches(nom, MOIS.keys(), n=1, cutoff=0.78)
    if proche:
        return f"{parts[1]}-{MOIS[proche[0]]:02d}"
    return None


def normalise_valeur(raw):
    v = raw.replace(",", ".").replace(" ", "")
    if v in ("", "-", "--"):
        return None
    try:
        float(v)
    except ValueError:
        return None
    return v


def est_entete(row):
    cells = [c for c in row[1:] if c]
    if not cells:
        return False
    if all(c.lower() == "valeur" for c in cells):
        return False
    return all(normalise_valeur(c) is None for c in cells)


def extract(page, energie, url, digest, observed_at):
    rows = parse_table(page)
    header = None
    out = []
    anomalies = []
    vues = {}
    for row in rows:
        if not row:
            continue
        periode = normalise_periode(row[0])
        if periode is None:
            if header is None and est_entete(row):
                header = list(row[1:])
            elif any(normalise_valeur(c) is not None for c in row[1:]):
                anomalies.append(row[0])
            continue
        if header is None:
            anomalies.append(f"valeurs avant en-tete: {row[0]}")
            continue
        if periode in vues:
            if row[1:] != vues[periode]:
                anomalies.append(
                    f"doublon contradictoire {periode}: {vues[periode]} vs {row[1:]}"
                )
            continue
        vues[periode] = row[1:]
        for i, cell in enumerate(row[1:]):
            if i >= len(header):
                break
            valeur = normalise_valeur(cell)
            if valeur is None:
                continue
            out.append({
                "observed_at": observed_at,
                "energie": energie,
                "mois": periode,
                "indice": header[i].strip(),
                "valeur": valeur,
                "source": url,
                "sha256": digest[:16],
            })
    return out, anomalies


def load_known():
    known = {}
    if not os.path.exists(OBS):
        return known
    with open(OBS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            known[(row["energie"], row["mois"], row["indice"])] = row["valeur"]
    return known


def append(rows):
    new = not os.path.exists(OBS)
    with open(OBS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def main():
    os.makedirs(SNAP, exist_ok=True)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = date.today().isoformat()
    known = load_known()
    ajouts, revisions, anomalies = [], [], []
    inchanges = 0

    for energie, url in PAGES.items():
        page = fetch(url)
        digest = hashlib.sha256(page.encode("utf-8")).hexdigest()
        path = os.path.join(SNAP, f"{today}-{energie}.html")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(page)
        lignes, anos = extract(page, energie, url, digest, observed_at)
        anomalies += [f"{energie}: {a}" for a in anos]
        for row in lignes:
            key = (row["energie"], row["mois"], row["indice"])
            if key not in known:
                ajouts.append(row)
            elif known[key] != row["valeur"]:
                revisions.append((key, known[key], row["valeur"]))
                ajouts.append(row)
            else:
                inchanges += 1

    append(ajouts)
    connues = [a for a in anomalies if "doublon contradictoire" in a]
    bloquantes = [a for a in anomalies if a not in connues]
    rapport = {
        "observed_at": observed_at,
        "nouvelles_valeurs": len(ajouts) - len(revisions),
        "revisions": [
            {"cle": "/".join(k), "avant": a, "apres": b} for k, a, b in revisions
        ],
        "inchangees": inchanges,
        "anomalies": bloquantes,
        "signalements": connues,
        "details": [
            f"{r['energie']}/{r['mois']}/{r['indice']} = {r['valeur']}" for r in ajouts
        ][:40],
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 1 if bloquantes else 0


if __name__ == "__main__":
    sys.exit(main())
