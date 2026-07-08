-- Asset-inventory / CBOM schema for the PQ-REACT recommendation engine.
-- Lives in the existing PQREACT MariaDB database alongside performance_test
-- and the regulatory tables. Idempotent.
--
-- Populated in Phase 3 by the asset_inventory REST API on <cbomkit-host>;
-- the corresponding MCP tools (query_crypto_inventory et al.) are read-only
-- and gracefully report "no CBOMs yet" while these tables are empty.

USE PQREACT;

CREATE TABLE IF NOT EXISTS cbom_documents (
    id              INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_system   VARCHAR(128)  NOT NULL,
    scan_date       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sector          VARCHAR(32)   NULL,
    scan_tool       VARCHAR(64)   NOT NULL,
    cbom_json       MEDIUMTEXT,
    INDEX idx_cbom_sector (sector),
    INDEX idx_cbom_scan_date (scan_date),
    INDEX idx_cbom_source (source_system)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crypto_assets (
    id                  INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cbom_doc_id         INT           NOT NULL,
    asset_type          VARCHAR(32)   NOT NULL,
    name                VARCHAR(255)  NOT NULL,
    version             VARCHAR(64),
    quantum_risk_level  VARCHAR(16)   NOT NULL,
    component_path      VARCHAR(512),
    sector              VARCHAR(32)   NULL,
    INDEX idx_asset_type (asset_type),
    INDEX idx_asset_risk (quantum_risk_level),
    INDEX idx_asset_sector (sector),
    INDEX idx_asset_name (name),
    CONSTRAINT fk_asset_cbom
        FOREIGN KEY (cbom_doc_id) REFERENCES cbom_documents(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS compliance_results (
    id              INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cbom_doc_id     INT           NOT NULL,
    policy_name     VARCHAR(128)  NOT NULL,
    pass_count      INT           NOT NULL DEFAULT 0,
    fail_count      INT           NOT NULL DEFAULT 0,
    findings_json   MEDIUMTEXT,
    scan_date       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_compl_policy (policy_name),
    INDEX idx_compl_scan_date (scan_date),
    CONSTRAINT fk_compl_cbom
        FOREIGN KEY (cbom_doc_id) REFERENCES cbom_documents(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
