-- UC3 (Campaign A) run audit log — Phase 7b of the recommendation engine.
-- Every uc3_run_bandwidth_validation / uc3_run_kpi_experiments invocation
-- lands here, regardless of whether the subprocess actually completes,
-- so a hung or killed run still leaves a forensic trail.
--
-- Insert order in the MCP tool:
--   1. INSERT (started_at, kind, args_json, status='running')   -- BEFORE spawn
--   2. spawn subprocess
--   3. UPDATE (finished_at, exit_code, status, summary_json)    -- AFTER wait
-- The pre-spawn INSERT is the one that matters: a hung subprocess can
-- still be matched to its initiating chat session by run_id later.

USE PQREACT;

CREATE TABLE IF NOT EXISTS uc3_runs (
    id              INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    kind            VARCHAR(48)   NOT NULL,    -- 'bandwidth_validation' | 'kpi_experiments'
    started_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME      NULL,
    status          VARCHAR(16)   NOT NULL DEFAULT 'running',  -- running | ok | failed | timeout
    exit_code       INT           NULL,
    args_json       TEXT          NULL,        -- input args passed to the tool, JSON
    summary_json    MEDIUMTEXT    NULL,        -- parsed JSON output from the run (truncated)
    artifact_path   VARCHAR(512)  NULL,        -- absolute path of the run's primary JSON artifact
    requested_by    VARCHAR(64)   NULL,        -- 'closed_loop' / 'manual' / chat session id
    notes           TEXT          NULL,        -- free-form (e.g. truncated stderr tail on failure)
    INDEX idx_uc3_runs_kind         (kind),
    INDEX idx_uc3_runs_started      (started_at),
    INDEX idx_uc3_runs_status       (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
