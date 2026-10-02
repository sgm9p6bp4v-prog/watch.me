# client--francesco-network-monitor — Projektkontext

## Zweck

NetWatch Light ist der lauffaehige, bewusst begrenzte MVP fuer einen lokalen
Netzwerkmonitor. Er visualisiert SNMP- und Disguise-SMC-Inventar,
Interface-Zustaende, LLDP-Topologie, Alerts und Events. `TECHNICAL_AUDIT.md` beschreibt die spaetere
Zielarchitektur; es ist nicht die Beschreibung des heutigen Runtime-Standes.

## Architektur

Der aktuelle Stand ist eine einzelne FastAPI-Anwendung:

```text
Browser UI in web/
  -> REST /api/* und WebSocket /ws/events
  -> netwatch_light.main
  -> NetWatchState
  -> SQLAlchemy/SQLite plus JSON-Kompatibilitaetssnapshot
  -> separater Poller-Prozess netwatch_light.poller
  -> Live-SNMP-Adapter netwatch_light.snmp_live
  -> Read-only-Disguise-SMC-Adapter netwatch_light.smc
```

Mock-Inventar, echte SNMP-Seeds und SMC-Chassis teilen denselben State. Der
separate Poller liest beide gespeicherten Quelltypen. SMC-NICs werden per MAC
mit LLDP-Kandidaten zusammengefuehrt; die Verkehrszaehler stammen weiterhin von
der passenden Switch-Port-Schnittstelle.

## Setup

`scripts/run_light_app.sh` legt bei Bedarf `.venv` an, installiert
`requirements.txt` und startet Uvicorn auf `127.0.0.1:5173`. Installationen und
Downloads nur nach Freigabe. Wenn die Umgebung bereits vorhanden ist:

```bash
.venv/bin/python -m uvicorn netwatch_light.main:app \
  --host 127.0.0.1 --port 5173
```

UI: `http://127.0.0.1:5173/`.

## Betrieb

Der Light-Build ist nur fuer lokale/LAN-Tests vorgesehen und besitzt keinen
Produktiv-Deploy. Runtime-State liegt standardmaessig in `data/netwatch.db` und
ist von Git ausgeschlossen. SNMP-Zugangsdaten werden verschluesselt gespeichert.

Runbook: [`00_infos/runbooks/local-light-app.md`](runbooks/local-light-app.md).

## Schnittstellen

- `GET /api/health` und `GET /api/snapshot`
- `POST /api/poll`, `/api/discovery`, `/api/polling`
- `POST /api/live/seed`, `/api/live/smc`, `/api/live/clear`
- `POST /api/topology/layout`
- Alert-Aktionen unter `/api/alerts/{id}/ack|resolve`
- WebSocket `/ws/events`
- SNMP v2c/v3 gegen explizit konfigurierte Seed-Hosts; kein Subnet-Scan
- Disguise SMC API read-only gegen explizit konfigurierte Hosts

## Abhaengigkeiten und Grenzen

Runtime-Abhaengigkeiten stehen in `requirements.txt`. Externe Runtime-Services
sind fuer den Light-Build nicht erforderlich. Der produktive Entwurf mit
separatem Worker, Redis und TimescaleDB ist erst nach Umsetzung und Tests als
realer Betriebsweg zu dokumentieren.
