-- Device crypto specifications — Phase 2 of the recommendation engine.
-- Schema captured from the testbed MCP VM (table existed there, 18 rows,
-- with no schema file in the public repo).
-- Single table in the existing PQREACT MariaDB; populated by
-- etl/import_device_specs.py from etl/data/device_crypto_specs.yaml.

USE PQREACT;

CREATE TABLE IF NOT EXISTS device_crypto_specs (
    id                       INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    device_type              VARCHAR(64)   NOT NULL,
    protocol                 VARCHAR(255)  NOT NULL,
    sector                   VARCHAR(32)   NOT NULL,
    current_cipher_suite     VARCHAR(255),
    current_key_exchange     VARCHAR(128),
    current_signature        VARCHAR(128),
    key_length               VARCHAR(255),
    pqc_compatible           BOOLEAN       NOT NULL DEFAULT FALSE,
    pqc_candidate_algorithms JSON          NULL,
    spec_reference           VARCHAR(255),
    source                   VARCHAR(64)   NOT NULL,
    notes                    TEXT,
    UNIQUE KEY uniq_device_protocol (device_type, protocol),
    INDEX idx_dcs_sector (sector),
    INDEX idx_dcs_pqc_compat (pqc_compatible),
    INDEX idx_dcs_device_type (device_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
