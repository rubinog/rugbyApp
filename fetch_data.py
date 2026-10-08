#!/usr/bin/env python3
"""Scarica i dati del Match Centre FIR (Serie A maschile) e li salva in data.json.

Uso:
    python fetch_data.py                      # scarica dal sito e scrive data.json
    python fetch_data.py --out web/data.json  # percorso di output diverso
    python fetch_data.py --from-file r.json   # test offline su un JSON già salvato

Solo libreria standard, nessuna dipendenza.
Se il download fallisce, il data.json precedente NON viene toccato.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# id = "52" + "2026" + "2027" (a quanto pare: categoria + stagione).
# Per la stagione successiva andrà aggiornato (da verificare nei DevTools).
API_URL = (
    "https://www.federugby.it/matchcentre/firapi.php"
    "?action=dettagli&id=52220262027&session_id=2027"
)
USER_AGENT = "serie-a-rugby-webapp/0.1 (progetto personale, aggiornamento ogni poche ore)"

# Suffissi societari da togliere per ottenere un nome breve leggibile.
# Sono stringhe letterali, dalla più lunga alla più corta.
_SUFFISSI = [
    "SOC.COOP.S.D.", "SOC.COOP. SD", "SOC.COOP.", "SSD A R.L.", "SSD S.R.L.",
    "SSD ARL", "A.S.D.", "S.S.D.", "S.R.L.", "A R.L.", "ASD", "SSD", "ARL", "SD",
]
_SUFFIX = re.compile(
    r"(?<![\w.])(?:" + "|".join(re.escape(x) for x in _SUFFISSI) + r")(?![\w])",
    re.IGNORECASE,
)
# Eccezioni manuali: nome ufficiale -> nome da mostrare. Da riempire a piacere.
NOMI_BREVI = {
    # "VII RUGBY TORINO SSD A R.L.": "VII Rugby Torino",
}


def nome_breve(ufficiale: str) -> str:
    if ufficiale in NOMI_BREVI:
        return NOMI_BREVI[ufficiale]
    s = _SUFFIX.sub("", ufficiale)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s.title()


def team_id(ufficiale: str) -> str:
    """ID stabile per squadra (usato per classifiche, filtri, preferiti)."""
    return re.sub(r"[^a-z0-9]+", "-", ufficiale.lower()).strip("-")


def parse_punteggio(p: str):
    """'24:17' -> (24, 17); '-:-' o vuoto -> (None, None)."""
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", p or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_data(data: str, ora: str):
    """'18/10/2026' + '15:30' -> '2026-10-18T15:30' (ora locale italiana)."""
    try:
        d = datetime.strptime(f"{data.strip()} {ora.strip()}", "%d/%m/%Y %H:%M")
        return d.strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def normalizza(raw: dict) -> dict:
    result = raw["result"]
    out = {
        "categoria": result.get("categoria"),
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fasi": [],
    }
    for fase in result.get("fasi", []):
        f = {"nome": fase.get("fase"), "gironi": []}
        for girone in fase.get("gironi", []):
            g = {
                "nome": girone.get("girone"),
                # Formato non ancora noto (vuota finché non si gioca):
                # la passiamo così com'è e la mapperemo alla prima giornata.
                "classifica_raw": girone.get("classifica", []),
                "squadre": {},
                "giornate": [],
            }
            for com in girone.get("comunicati", []):
                for tipo in com.get("tipi_incontro", []):
                    for gio in tipo.get("giornate", []):
                        partite = []
                        for p in gio.get("partite", []):
                            sc_h, sc_g = parse_punteggio(p.get("punteggio"))
                            h, a = p["home_nome"], p["guest_nome"]
                            for nome in (h, a):
                                g["squadre"][team_id(nome)] = {
                                    "ufficiale": nome,
                                    "nome": nome_breve(nome),
                                }
                            partite.append({
                                "inizio": parse_data(p.get("data", ""), p.get("ora", "")),
                                "casa": team_id(h),
                                "ospite": team_id(a),
                                "punti_casa": sc_h,
                                "punti_ospite": sc_g,
                                "giocata": sc_h is not None,
                                "campo": (p.get("campo") or "").strip() or None,
                                "provvisorio": bool((p.get("provvisorio") or "").strip()),
                                "rettificato": bool((p.get("rettificato") or "").strip()),
                            })
                        partite.sort(key=lambda x: x["inizio"] or "")
                        g["giornate"].append({
                            "nome": gio.get("giornata"),
                            "tipo": tipo.get("tipo_incontro"),  # Andata / Ritorno
                            "partite": partite,
                        })
            f["gironi"].append(g)
        out["fasi"].append(f)
    return out


def scarica() -> dict:
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8-sig")
    raw = json.loads(body)
    if not raw.get("success"):
        raise RuntimeError("Risposta API con success=false")
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--from-file", help="usa un JSON locale invece di scaricare")
    args = ap.parse_args()

    try:
        if args.from_file:
            raw = json.loads(Path(args.from_file).read_text(encoding="utf-8-sig"))
        else:
            raw = scarica()
        dati = normalizza(raw)
    except Exception as e:  # rete, JSON, struttura cambiata...
        print(f"ERRORE: {e!r} - data.json esistente lasciato invariato", file=sys.stderr)
        return 1

    out = Path(args.out)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)  # scrittura atomica

    n_gironi = sum(len(f["gironi"]) for f in dati["fasi"])
    n_partite = sum(len(gi["partite"]) for f in dati["fasi"] for g in f["gironi"] for gi in g["giornate"])
    print(f"OK: {n_gironi} gironi, {n_partite} partite -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
