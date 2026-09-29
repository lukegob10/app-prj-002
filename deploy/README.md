# Agora homelab deployment

This deployment runs the FastAPI backend, a separate API dataset scheduler,
and the Angular static frontend as three Docker services. The backend and
scheduler join the existing `oracle-free_default` Docker
network and use the configured Oracle schema. Nginx Proxy Manager routes
`agora.home` to the frontend on host port 8082; AdGuard supplies the local DNS
rewrite.

The backend image accepts an optional `TREASURY_ANALYTICS_PACKAGE` build
argument and a BuildKit secret named `pip_extra_index_url`. Set
`AGORA_USE_TREASURY_ANALYTICS=1` only when the real package is available and
its `treasury_analytics.TAConnection` API has been confirmed. With the current
workspace, that distribution is not installed, so the verified `oracledb`
connection path remains active.

Apply `python -m agora.core.migrate_api_datasets` and
`python -m agora.core.migrate_api_dataset_schedules` against an existing
database before starting these updated services. New installations use the
updated `backend/schema.sql` baseline. Both backend and scheduler need the
same `API_DATA_ENCRYPTION_KEY` in the private `.env.runtime`; keep that key
backed up with the database. The scheduler uses the backend image and runs
`python -m agora.data.scheduler_worker`. It must stay running for configured
refreshes to occur. Oracle coordinates job claims when more than one worker
runs, and the app's Data tab shows recent runs and errors. A draft refresh
needs an owner to publish the resulting version; owner-enabled auto-publish
can advance the live dashboard after a successful refresh.

The runtime `.env.runtime` and build secret are intentionally local files and
must never be committed. The homelab HTTP switch is explicit because this
network does not currently have a TLS certificate for `agora.home`.
