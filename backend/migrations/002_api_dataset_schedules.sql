-- Scheduled refreshes for saved API datasets. Apply after 001_api_datasets.sql.
ALTER TABLE TB_TA_AGORA_API_DATASETS ADD CONSTRAINT UQ_TA_API_DATASET_PROJECT_ID
    UNIQUE (project_id, id)
/

CREATE TABLE TB_TA_AGORA_API_DATASET_SCHEDULES (
    connection_id       VARCHAR2(36 BYTE) NOT NULL,
    project_id          VARCHAR2(36 BYTE) NOT NULL,
    enabled             NUMBER(1) DEFAULT 0 NOT NULL,
    frequency           VARCHAR2(10 BYTE) NOT NULL,
    interval_minutes    NUMBER(5),
    local_time          VARCHAR2(5 BYTE),
    weekdays_json       CLOB,
    day_of_month        NUMBER(2),
    timezone            VARCHAR2(64 BYTE) NOT NULL,
    base_version_id     VARCHAR2(36 BYTE) NOT NULL,
    publish_mode        VARCHAR2(16 BYTE) NOT NULL,
    publish_approved_by VARCHAR2(36 BYTE),
    next_run_at         TIMESTAMP WITH TIME ZONE,
    last_run_at         TIMESTAMP WITH TIME ZONE,
    created_by          VARCHAR2(36 BYTE) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT PK_TA_API_DATASET_SCHED PRIMARY KEY (connection_id),
    CONSTRAINT UQ_TA_API_SCHED_PROJECT_CONN UNIQUE (project_id, connection_id),
    CONSTRAINT FK_TA_API_SCHED_CONN FOREIGN KEY (project_id, connection_id)
        REFERENCES TB_TA_AGORA_API_DATASETS (project_id, id) ON DELETE CASCADE,
    CONSTRAINT FK_TA_API_SCHED_PROJECT FOREIGN KEY (project_id)
        REFERENCES TB_TA_AGORA_PROJECTS (id) ON DELETE CASCADE,
    CONSTRAINT FK_TA_API_SCHED_BASE_VERSION FOREIGN KEY (project_id, base_version_id)
        REFERENCES TB_TA_AGORA_CONTENT_VERSIONS (project_id, id),
    CONSTRAINT FK_TA_API_SCHED_APPROVER FOREIGN KEY (publish_approved_by)
        REFERENCES TB_TA_AGORA_USERS (id),
    CONSTRAINT FK_TA_API_SCHED_CREATOR FOREIGN KEY (created_by)
        REFERENCES TB_TA_AGORA_USERS (id),
    CONSTRAINT CK_TA_API_SCHED_ENABLED CHECK (enabled IN (0, 1)),
    CONSTRAINT CK_TA_API_SCHED_FREQUENCY CHECK (frequency IN ('interval', 'daily', 'weekly', 'monthly')),
    CONSTRAINT CK_TA_API_SCHED_INTERVAL CHECK (interval_minutes IS NULL OR interval_minutes BETWEEN 15 AND 10080),
    CONSTRAINT CK_TA_API_SCHED_DAY CHECK ((frequency = 'monthly' AND day_of_month IS NOT NULL AND day_of_month BETWEEN 1 AND 28) OR (frequency <> 'monthly' AND day_of_month IS NULL)),
    CONSTRAINT CK_TA_API_SCHED_RECURRENCE CHECK ((frequency = 'interval' AND interval_minutes IS NOT NULL AND local_time IS NULL) OR (frequency IN ('daily', 'weekly', 'monthly') AND interval_minutes IS NULL AND local_time IS NOT NULL)),
    CONSTRAINT CK_TA_API_SCHED_PUBLISH CHECK (publish_mode IN ('draft', 'auto_publish')),
    CONSTRAINT CK_TA_API_SCHED_WEEKDAYS_JSON CHECK (weekdays_json IS JSON)
)
/
CREATE INDEX IX_TA_API_SCHED_DUE ON TB_TA_AGORA_API_DATASET_SCHEDULES (enabled, next_run_at)
/
COMMENT ON TABLE TB_TA_AGORA_API_DATASET_SCHEDULES IS 'Per-connection timezone-aware recurrence and owner-approved publication policy.'
/

CREATE TABLE TB_TA_AGORA_API_DATASET_RUNS (
    id                       VARCHAR2(36 BYTE) NOT NULL,
    connection_id            VARCHAR2(36 BYTE) NOT NULL,
    project_id               VARCHAR2(36 BYTE) NOT NULL,
    status                   VARCHAR2(16 BYTE) NOT NULL,
    is_manual                NUMBER(1) DEFAULT 0 NOT NULL,
    scheduled_for            TIMESTAMP WITH TIME ZONE NOT NULL,
    available_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    attempt_count            NUMBER(2) DEFAULT 0 NOT NULL,
    claim_token              VARCHAR2(36 BYTE),
    lease_until              TIMESTAMP WITH TIME ZONE,
    connection_updated_at    TIMESTAMP WITH TIME ZONE,
    base_version_id          VARCHAR2(36 BYTE) NOT NULL,
    source_version_id        VARCHAR2(36 BYTE),
    expected_published_id    VARCHAR2(36 BYTE),
    expected_publication_at  TIMESTAMP WITH TIME ZONE,
    publish_mode             VARCHAR2(16 BYTE) NOT NULL,
    version_actor_id         VARCHAR2(36 BYTE) NOT NULL,
    publish_actor_id         VARCHAR2(36 BYTE),
    requested_by             VARCHAR2(36 BYTE),
    started_at               TIMESTAMP WITH TIME ZONE,
    finished_at              TIMESTAMP WITH TIME ZONE,
    version_id               VARCHAR2(36 BYTE),
    snapshot_id              VARCHAR2(36 BYTE),
    published                NUMBER(1),
    unchanged                NUMBER(1) DEFAULT 0 NOT NULL,
    error_code               VARCHAR2(80 BYTE),
    error_message            VARCHAR2(500 CHAR),
    created_at               TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT PK_TA_API_DATASET_RUNS PRIMARY KEY (id),
    CONSTRAINT FK_TA_API_RUN_SCHEDULE FOREIGN KEY (project_id, connection_id)
        REFERENCES TB_TA_AGORA_API_DATASET_SCHEDULES (project_id, connection_id) ON DELETE CASCADE,
    CONSTRAINT FK_TA_API_RUN_PROJECT FOREIGN KEY (project_id)
        REFERENCES TB_TA_AGORA_PROJECTS (id) ON DELETE CASCADE,
    CONSTRAINT FK_TA_API_RUN_BASE_VERSION FOREIGN KEY (project_id, base_version_id)
        REFERENCES TB_TA_AGORA_CONTENT_VERSIONS (project_id, id),
    CONSTRAINT FK_TA_API_RUN_SOURCE_VERSION FOREIGN KEY (project_id, source_version_id)
        REFERENCES TB_TA_AGORA_CONTENT_VERSIONS (project_id, id),
    CONSTRAINT FK_TA_API_RUN_PUBLISHED_EXPECTED FOREIGN KEY (project_id, expected_published_id)
        REFERENCES TB_TA_AGORA_CONTENT_VERSIONS (project_id, id),
    CONSTRAINT FK_TA_API_RUN_VERSION FOREIGN KEY (project_id, version_id)
        REFERENCES TB_TA_AGORA_CONTENT_VERSIONS (project_id, id),
    CONSTRAINT FK_TA_API_RUN_SNAPSHOT FOREIGN KEY (project_id, snapshot_id)
        REFERENCES TB_TA_AGORA_CSV_SNAPSHOTS (project_id, id),
    CONSTRAINT FK_TA_API_RUN_VERSION_ACTOR FOREIGN KEY (version_actor_id)
        REFERENCES TB_TA_AGORA_USERS (id),
    CONSTRAINT FK_TA_API_RUN_PUBLISH_ACTOR FOREIGN KEY (publish_actor_id)
        REFERENCES TB_TA_AGORA_USERS (id),
    CONSTRAINT FK_TA_API_RUN_REQUESTED_BY FOREIGN KEY (requested_by)
        REFERENCES TB_TA_AGORA_USERS (id),
    CONSTRAINT CK_TA_API_RUN_STATUS CHECK (status IN ('queued', 'running', 'succeeded', 'unchanged', 'failed', 'cancelled', 'needs_review')),
    CONSTRAINT CK_TA_API_RUN_MANUAL CHECK (is_manual IN (0, 1)),
    CONSTRAINT CK_TA_API_RUN_ATTEMPTS CHECK (attempt_count BETWEEN 0 AND 99),
    CONSTRAINT CK_TA_API_RUN_PUBLISH_MODE CHECK (publish_mode IN ('draft', 'auto_publish')),
    CONSTRAINT CK_TA_API_RUN_PUBLISHED CHECK (published IS NULL OR published IN (0, 1)),
    CONSTRAINT CK_TA_API_RUN_UNCHANGED CHECK (unchanged IN (0, 1))
)
/
CREATE INDEX IX_TA_API_RUN_QUEUE ON TB_TA_AGORA_API_DATASET_RUNS (status, available_at, scheduled_for)
/
CREATE INDEX IX_TA_API_RUN_CONN_TIME ON TB_TA_AGORA_API_DATASET_RUNS (connection_id, created_at)
/
COMMENT ON TABLE TB_TA_AGORA_API_DATASET_RUNS IS 'Sanitized scheduled and manual API refresh history with fenced worker leases.'
/
