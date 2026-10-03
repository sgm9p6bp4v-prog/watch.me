# NetWatch Light App

This is the first working lightweight version of NetWatch. It is intentionally smaller than the final architecture in `TECHNICAL_AUDIT.md`, but it already has a real FastAPI backend, REST endpoints, a WebSocket event stream and a browser UI.

## What works

- FastAPI backend.
- Static frontend served by the backend.
- Mock FS-like SNMP inventory.
- Live seed discovery against a real SNMP switch.
- Database persistence for inventory, links, alerts, events, settings, labels, layout and interface history.
- One-time migration from the legacy local JSON state file when the database is empty.
- Local database persistence for SNMP seed credentials in the light build.
- Multi-seed runtime polling with credentials reloaded after backend restart.
- Dedicated poller process with 30-second status/traffic polling and a 15-minute inventory cycle.
- Versioned metric catalog loaded from `config/metric_catalog.yaml`.
- Structured metric sample persistence for interface status, counters and traffic gauges.
- Poll run history with status, duration, seed count, successes and failures.
- Encrypted SNMP credential storage for the local database build.
- Local backup/restore commands for the SQLite DB, master key and config YAML files.
- Automatic backend tests for credential encryption, backup/restore and metric catalog behavior.
- Device list and device detail.
- LLDP topology with confirmed and pending links.
- Alert lifecycle: active, acknowledged, resolved.
- Automatic SNMP and SMC polling plus on-demand discovery through real API calls.
- WebSocket event stream.

## What is still mocked

- Redis/arq worker.
- Dedicated production PostgreSQL deployment.
- Docker secrets.

By default, the light app persists runtime state in `data/netwatch.db` using SQLite. Set `DATABASE_URL` to point to PostgreSQL when moving beyond local testing. If `data/netwatch_state.json` exists and the database is empty, it is imported automatically on startup.

SNMP communities and SNMPv3 secrets are encrypted before they are written to the local database. The app reads the master key from `NETWATCH_MASTER_KEY`, then `NETWATCH_MASTER_KEY_FILE`, and finally falls back to a generated local key at `data/netwatch_master.key`.

Keep the master key with the database backup. Losing the key means losing access to saved SNMP credentials.

## Run locally

Create the virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Optional database migration command:

```bash
.venv/bin/alembic upgrade head
```

Start the app:

```bash
.venv/bin/python -m uvicorn netwatch_light.main:app --host 127.0.0.1 --port 5173 --reload
```

Start the poller in a second terminal:

```bash
.venv/bin/python -m netwatch_light.poller
```

The web server serves the UI/API. The poller reads seed credentials from the same database and updates interface status, counters and traffic every 30 seconds without overlapping cycles. LLDP, VLAN, FDB, ARP and topology inventory is refreshed every 15 minutes or on demand. Saved SNMP/SMC sources are queried with bounded concurrency so a larger inventory does not become a long serial queue or overload the server.

The light build now also writes structured metric samples and poll run history into the local database. This is not a full TimescaleDB deployment yet, but it keeps metric names, labels and poller state explicit enough to migrate cleanly later.

Open:

```text
http://127.0.0.1:5173/
```

## Live FS switch test

On the switch, enable SNMP read-only and LLDP. From the app:

1. Open `Settings`.
2. Use `Clear mock data`.
3. Enter the switch management IP.
4. Choose SNMP `v2c` or `v3`.
5. Enter the read-only community or SNMPv3 user details.
6. Click `Test and import seed`.

The app reads:

- `SNMPv2-MIB`: `sysName`, `sysDescr`, `sysObjectID`.
- `IF-MIB` and `ifXTable`: interfaces, admin/oper state, counters.
- `LLDP-MIB`: one-sided neighbor candidates plus local/remote port evidence when available.

Use the `SNMP Seed` button in the dashboard presentation UI for the same workflow without opening the old sidebar shell.

## Backup and restore

Create a local backup archive:

```bash
scripts/backup_light_app.sh
```

Restore a backup archive:

```bash
scripts/restore_light_app.sh path/to/netwatch-light-backup.tar.gz --force
```

Stop the web server and poller before restoring. Restore preserves overwritten files with a `.pre-restore-<timestamp>` suffix.

## Tests

Run the automated backend tests:

```bash
.venv/bin/python -m pytest
```

Run the presentation scroll regression tests with Node.js (no npm dependencies):

```bash
node --test tests/test_presentation_scroll.mjs
```

Wheel scrolling follows the input distance, brakes at each section, and holds there
until further wheel input releases it (about three standard wheel steps). Reverse
input releases the hold immediately. Touch, keyboard and reduced-motion navigation
use native scrolling; cards, the device grid and overlays keep their own scrolling.
