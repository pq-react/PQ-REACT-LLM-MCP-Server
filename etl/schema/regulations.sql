-- Regulatory knowledge base schema for the PQ-REACT recommendation engine.
-- Lives in the existing PQREACT MariaDB database alongside performance_test.
-- Idempotent: every CREATE is IF NOT EXISTS.
--
-- Shape follows etl/data/regulations.yaml 1:1. The YAML's per-obligation
-- algorithm_map sub-object is flattened into columns on `obligations`
-- (1:1 nullable; saves a join in every read path).

USE PQREACT;

CREATE TABLE IF NOT EXISTS regulations (
    id              VARCHAR(64)   NOT NULL PRIMARY KEY,
    name            VARCHAR(128)  NOT NULL,
    long_name       TEXT,
    kind            VARCHAR(32)   NOT NULL,
    body            VARCHAR(64),
    jurisdiction    VARCHAR(32),
    effective_date  DATE          NULL,
    pqc_deadline    DATE          NULL,
    sector_scope    JSON          NULL,
    summary         TEXT,
    source_url      VARCHAR(512),
    INDEX idx_reg_pqc_deadline (pqc_deadline),
    INDEX idx_reg_jurisdiction (jurisdiction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS obligations (
    id                      INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    regulation_id           VARCHAR(64)   NOT NULL,
    article_ref             VARCHAR(255)  NOT NULL,
    obligation_text         TEXT          NOT NULL,
    obligation_type         VARCHAR(32)   NOT NULL,
    affected_sectors        JSON          NULL,
    deadline                DATE          NULL,
    crypto_relevance        TINYINT       NOT NULL,
    -- algorithm_map flattened (nullable group):
    nist_level_min          TINYINT       NULL,
    recommended_families    JSON          NULL,
    hybrid_required         BOOLEAN       NULL,
    algorithm_notes         TEXT,
    UNIQUE KEY uniq_obligation (regulation_id, article_ref),
    INDEX idx_obl_type (obligation_type),
    INDEX idx_obl_deadline (deadline),
    INDEX idx_obl_relevance (crypto_relevance),
    CONSTRAINT fk_obl_regulation
        FOREIGN KEY (regulation_id) REFERENCES regulations(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sector_profiles (
    sector_id               VARCHAR(32)   NOT NULL PRIMARY KEY,
    applicable_regulations  JSON          NULL,
    priority_assets         JSON          NULL,
    typical_crypto_surface  TEXT,
    migration_complexity    TINYINT       NULL,
    long_lived_data         BOOLEAN       NULL,
    notes                   TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
