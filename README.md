# Agora

Agora hosts and shares HTML dashboards and small browser apps produced outside the platform. An Angular shell manages accounts, projects, access, uploads and publication. FastAPI enforces authorization, persists metadata and files in Oracle, and exposes narrow data capabilities to isolated HTML viewers.

## Local setup

Requirements: Python 3.12, Node 22, npm, Oracle access configured for Agora, and PowerShell. Copy `.env.example` to `.env` and set `TA_PROD_PASSWORD` privately. Install the Agora backend and its Oracle and Trino drivers into `.venv`. The PROD connection is configured with `TA_PROD_USER`, `TA_PROD_PASSWORD`, and `TA_PROD_DSN`; Agora tables are created in that Oracle user's default schema. The schema names require Oracle 12.2+ with `COMPATIBLE >= 12.2`; use AL32UTF8 for full Unicode support. `ENV=PROD` selects the production code path. The local app origin is `http://localhost:4200`.

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ./backend
.venv/Scripts/python.exe -m agora.core.schema
cd frontend
npm ci
npm start
```

Run `.venv/Scripts/python.exe -m uvicorn agora.main:app --host 127.0.0.1 --port 8000` from the repository root in another terminal. Run the scheduler worker shown below in a third terminal when using scheduled API imports.

Open `http://localhost:4200`. Angular proxies `/api` to FastAPI on `127.0.0.1:8000`. `GET /api/health/live` reports process liveness; `GET /api/health/ready` checks Oracle and the baseline tables, named constraints, and declared indexes. Readiness returns 503 if credentials, Oracle, or the schema are unavailable. It never silently substitutes local storage or mock data.

Set `AGORA_PUBLIC_ORIGIN=http://localhost:4200` for this frontend. The scheme, hostname, and port must match the browser address; `127.0.0.1` and `localhost` are different origins. Local HTTP pages opened through a loopback IP redirect to `localhost` before sign-in. If running a second instance on another port, set that backend's `AGORA_PUBLIC_ORIGIN` to the matching localhost URL and restart that backend after changing it. An `origin_mismatch` error happens before password validation.

`backend/schema.sql` is the single baseline DDL for the connected Oracle user's default schema. It creates all `TB_TA_AGORA_` tables, named constraints, and workload indexes in dependency order. It requires Oracle 12.2 or later with `COMPATIBLE` set to at least 12.2 because the requested table names exceed Oracle's older 30-byte identifier limit. Run it once against an empty schema with `python -m agora.core.schema` or execute it as a script in Oracle SQL Developer. It does not drop or rename existing objects. The schema command reports an already-complete schema and refuses a partial schema or unrecognized/legacy Agora tables instead of guessing how to repair them.

## API dataset setup

In a project's **Data** tab, select **API connection**. Save an HTTPS endpoint using GET or POST, optional request headers (including Authorization or API keys), a JSON object payload, and a records path such as `data.items`. **Save and test** previews the returned records; **Import snapshot** creates a working dashboard version. Preview and publish it from Versions. API responses are bounded and pagination is not automatic.

An owner or editor can configure an interval, daily, weekly, or monthly refresh schedule for a saved connection. Daily, weekly, and monthly times use a selected IANA time zone; monthly runs can use days 1–28. A nonexistent local time during a daylight saving transition is skipped, and a repeated local time runs once at its first occurrence. A schedule can be enabled or paused, run on demand, and inspected through its recent run history. A changed result creates a new immutable CSV snapshot bound to a new dashboard version; an identical result is recorded as unchanged without another version. In **Draft** mode, an owner previews and publishes the new version before viewers see its data. In **Auto-publish** mode, which only an owner can enable, a successful changed run publishes its new version automatically. If someone changes the live version while the upstream request is running, the scheduled run does not replace that newer publication. The new data remains available as a working version for review.

Schedules run only while the separate worker process is running. Start it alongside the API service in another terminal for local development:

```powershell
.venv/Scripts/python.exe -m agora.data.scheduler_worker
```

The worker stores claims and run history in Oracle so multiple replicas can share the queue. Missed times are skipped after downtime; a GET request may receive bounded retries for transient network failures, while POST requests are not retried automatically.

API connections require a private `API_DATA_ENCRYPTION_KEY` in `.env`. Generate a Fernet key using the command in `.env.example`, keep it out of source control, and back it up alongside the database. Saved URLs, headers, and payloads are encrypted with this key. Losing or replacing the key makes existing connections unreadable. Endpoints must resolve to public IP addresses; local/private hosts and redirects are rejected.

For an existing Agora database, install the updated backend dependencies and apply the additive migrations, then restart the backend and scheduler:

```powershell
.venv/Scripts/python.exe -m pip install -e ./backend
.venv/Scripts/python.exe -m agora.core.migrate_api_datasets
.venv/Scripts/python.exe -m agora.core.migrate_api_dataset_schedules
.venv/Scripts/python.exe -m uvicorn agora.main:app --host 127.0.0.1 --port 8000
```

Run both migrations before starting the updated API and worker. New databases receive the tables through `backend/schema.sql`. The migrations leave existing projects, versions, and snapshots intact and can be rerun safely. Back up `API_DATA_ENCRYPTION_KEY`: the worker needs the same key as the API process to read saved connections.

## CSV snapshots and dashboard edits

CSV uploads are immutable and bound to dashboard versions. The Data tab lets editors browse snapshots and page through their rows. Replacing a CSV creates a working version that must be previewed and published; it does not edit the published snapshot in place.

Uploaded HTML reads the bound CSV through `Agora.csv()`. To save edits from a dashboard, use `Agora.records.create()`, `update()`, or `delete()` and merge those saved records with the CSV when rendering. Owners and editors can write records; viewers can write only when the project owner enables viewer writes. Saved records appear separately in the Data tab and do not modify CSV downloads or previews.

## Accounts and recovery

Self-registration takes a unique username, full name and password. A platform administrator cannot be self-assigned. To establish the first admin, with Oracle credentials already configured, run:

```powershell
.venv/Scripts/python.exe -m agora.core.admin_cli bootstrap-admin --username admin --full-name "Platform Administrator"
```

The CLI prompts for the password without echo. It only runs while no admin account exists. For account recovery, an operator verifies the person's identity outside Agora and runs:

```powershell
.venv/Scripts/python.exe -m agora.core.admin_cli reset-password --username existing_user
```

This revokes existing sessions and writes an audit event. A signed-in platform administrator can also reset a user's password through the admin API after identity verification. There is no unauthenticated public reset shortcut.

## Architecture

The backend stores projects, versions, CSV snapshots, and records in shared Oracle tables. Owners manage members and publication; editors upload and preview; viewers see published projects. Uploaded content runs in an isolated iframe. See [architecture](docs/ARCHITECTURE.md) for the Oracle and Starburst paths and what the viewer SDK does.

The product direction is in [Platform vision](docs/PLATFORM_VISION.md). See [deployment](deploy/README.md) for the Docker Compose setup.
