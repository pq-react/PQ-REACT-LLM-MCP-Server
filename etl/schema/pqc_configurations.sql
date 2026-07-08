-- PQC configuration audit log — Phase 5 of the recommendation engine.
-- Every apply_pqc_configuration call lands here, regardless of dry_run.
-- Lets the closed-loop orchestrator (CAM closed_loop.py) and the
-- recommendation engine itself reconstruct the full
--   recommendation → application → verification
-- chain after the fact.

USE PQREACT;

CREATE TABLE IF NOT EXISTS pqc_configurations (
    id              INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sector          VARCHAR(32)   NOT NULL,
    algorithm       VARCHAR(128)  NOT NULL,
    target_type     VARCHAR(32)   NOT NULL,    -- audit | qujata-experiment | slice
    target_ref      VARCHAR(255),               -- slice_id, experiment name, or free-form note
    rationale       TEXT,                       -- why this algorithm (forwarded from recommend_migration_priority)
    dry_run         BOOLEAN       NOT NULL DEFAULT TRUE,
    applied_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    qujata_suite_id INT           NULL,         -- when target_type='qujata-experiment'
    requested_by    VARCHAR(64)   NULL,         -- 'closed_loop' / 'manual' / chat session id
    notes           TEXT,
    INDEX idx_pqc_cfg_sector       (sector),
    INDEX idx_pqc_cfg_target       (target_type, target_ref),
    INDEX idx_pqc_cfg_applied      (applied_at),
    INDEX idx_pqc_cfg_qujata_suite (qujata_suite_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
