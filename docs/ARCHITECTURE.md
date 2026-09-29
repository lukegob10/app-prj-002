# Application architecture

Agora has three runtime parts: the Angular control app in `frontend/`, the FastAPI service in `backend/`, and the browser bridge in `viewer-sdk/`.

## Storage and data connections

- The backend owns the app’s Oracle connection through `agora.core.db` and `python-oracledb`. App tables live together in the connected user's default Oracle schema and use the `TB_TA_AGORA_` prefix; projects do not get separate physical tables.
- `backend/schema.sql` is the one-time baseline for a new, empty schema. It does not migrate, drop, or rename existing objects.
- External analytics reads use `agora.data.sources` and `agora.data.starburst` with the Trino driver. Credentials stay on the backend; requests are limited to approved sources, discovered tables, and bounded row results.

## API datasets

Project editors can choose **CSV file** or **API connection** in the project's Data setup. An API connection describes a JSON endpoint, GET or POST method, request headers, optional JSON payload, and an optional path to the response's array of records.

Testing a connection previews the returned rows. Importing fetches the endpoint and creates an immutable CSV snapshot attached to a new working version of the dashboard. Preview and publish that version through the existing Versions workflow. The existing version-bound CSV SDK reads imported data without receiving connection credentials.

An optional schedule can import on a bounded interval or at a local daily, weekly, or monthly time in an IANA time zone. The scheduler is a separate process (`python -m agora.data.scheduler_worker`) with Oracle-backed claims and run history. Multiple worker instances coordinate through Oracle; missed schedule times are skipped instead of replayed in a burst. Only sanitized error codes and messages are exposed in run history. Saved API URLs, headers, and payloads remain encrypted in Oracle and are decrypted only by the worker when making the outbound HTTPS request.

A scheduled import with changed data creates a new version-bound snapshot; identical data is recorded as unchanged without creating another version. Draft mode leaves a changed version for an owner to preview and publish. Owner-enabled auto-publish mode advances the project's published version after a successful changed import, provided the published pointer has not changed while the request was in flight. That check prevents a scheduled run from overwriting a more recent human publication. Opening a dashboard never calls the upstream API; viewers read the snapshot bound to the published version.

## Viewer and SDK

The viewer SDK does **not** render HTML. Uploaded HTML and its own JavaScript render inside an iframe. A dashboard can render without `sdk.js` if it does not need Agora data.

- `viewer-sdk/host.js` runs in the trusted Angular page. The project page uses it to create the sandboxed iframe and establish the data bridge.
- `viewer-sdk/sdk.js` is optional for each uploaded dashboard. Include `<script src="/__agora/sdk.js"></script>` only when the dashboard needs the `window.Agora` API for version-bound CSV, project records, or approved Starburst rows.

The frame runs with scripts enabled but without `allow-same-origin`. The host and dashboard exchange scoped messages through a `MessageChannel`; the host makes authenticated API requests. The dashboard never receives the app’s session cookie, CSRF token, database credentials, or arbitrary SQL access. Backend authorization still applies to every data request.

## Local development

Use the commands in the root README to install the backend, create a new schema, and start FastAPI and Angular. The Angular dev server runs on `localhost:4200` and proxies API requests to FastAPI on `127.0.0.1:8000`. Start the scheduler worker in another terminal when using schedules. The Docker Compose deployment is in `deploy/`.
