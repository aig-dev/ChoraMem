-- Memory Core MySQL 8 V1: complete fresh-install causal schema.
-- Stable refs are bytewise identifiers and bounded to 384 UTF-8 bytes by Core.

CREATE TABLE IF NOT EXISTS source_events (
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    source_ref VARBINARY(384) NOT NULL,
    actor_kind VARBINARY(32) NOT NULL,
    actor_ref VARBINARY(384) NOT NULL,
    source_text LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    PRIMARY KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref
    ),
    CHECK (OCTET_LENGTH(tenant_ref) > 0 AND OCTET_LENGTH(agent_ref) > 0 AND OCTET_LENGTH(source_ref) > 0),
    CHECK (OCTET_LENGTH(source_text) > 0),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (actor_kind IN ('agent', 'user', 'system', 'tool', 'external')),
    CHECK (actor_ref <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS episodes (
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    run_ref VARBINARY(384) NOT NULL,
    source_group_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL,
    PRIMARY KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, run_ref, source_group_ref
    ),
    UNIQUE KEY episodes_ref_uq (episode_ref),
    UNIQUE KEY episodes_tenant_episode_ref_uq (tenant_ref, episode_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> ''),
    CHECK (run_ref <> '' AND source_group_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS episode_links (
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    run_ref VARBINARY(384) NOT NULL,
    source_group_ref VARBINARY(384) NOT NULL,
    source_event_ref VARBINARY(384) NOT NULL,
    role VARBINARY(32) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL DEFAULT '',
    PRIMARY KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
        run_ref, source_group_ref, source_event_ref, role
    ),
    KEY episode_links_group_gate_idx (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
        run_ref, source_group_ref, role, source_event_ref
    ),
    KEY episode_links_episode_ref_idx (episode_ref),
    CONSTRAINT episode_links_source_fk FOREIGN KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_event_ref
    ) REFERENCES source_events (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref
    ),
    CHECK (run_ref <> '' AND source_group_ref <> ''),
    CHECK (role IN ('situation', 'agent_act', 'outcome'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS request_receipts (
    tenant_ref VARBINARY(384) NOT NULL,
    idempotency_key VARBINARY(384) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    source_event_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_ref, idempotency_key),
    CHECK (tenant_ref <> '' AND idempotency_key <> ''),
    CHECK (source_event_ref <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS disposition_seeds (
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    seed_ref VARBINARY(384) NOT NULL,
    PRIMARY KEY (tenant_ref, seed_ref),
    UNIQUE KEY disposition_seeds_ref_uq (seed_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> '' AND seed_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS seed_versions (
    tenant_ref VARBINARY(384) NOT NULL,
    seed_ref VARBINARY(384) NOT NULL,
    seed_version_ref VARBINARY(384) NOT NULL,
    version_number INT NOT NULL,
    tendency_text LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    status VARBINARY(32) NOT NULL,
    origin_job_ref VARBINARY(384) NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref),
    UNIQUE KEY seed_versions_ref_uq (seed_version_ref),
    UNIQUE KEY seed_versions_number_uq (tenant_ref, seed_ref, version_number),
    KEY seed_versions_origin_job_idx (tenant_ref, origin_job_ref, seed_version_ref),
    CONSTRAINT seed_versions_seed_fk FOREIGN KEY (tenant_ref, seed_ref)
        REFERENCES disposition_seeds (tenant_ref, seed_ref),
    CHECK (version_number > 0),
    CHECK (OCTET_LENGTH(tendency_text) > 0),
    CHECK (status IN ('active', 'superseded', 'ineligible')),
    CHECK (origin_job_ref <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS seed_basis_links (
    tenant_ref VARBINARY(384) NOT NULL,
    seed_version_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL,
    role VARBINARY(32) NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref, episode_ref, role),
    KEY seed_basis_links_episode_idx (tenant_ref, episode_ref, seed_version_ref),
    CONSTRAINT seed_basis_links_version_fk FOREIGN KEY (tenant_ref, seed_version_ref)
        REFERENCES seed_versions (tenant_ref, seed_version_ref),
    CONSTRAINT seed_basis_links_episode_fk FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (role IN ('formation', 'revision', 'reenactment', 'inhibition'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS consolidation_receipts (
    tenant_ref VARBINARY(384) NOT NULL,
    job_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    request_hash BINARY(32) NOT NULL,
    PRIMARY KEY (tenant_ref, job_ref),
    CHECK (tenant_ref <> '' AND job_ref <> '' AND agent_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS outcome_events (
    tenant_ref VARBINARY(384) NOT NULL,
    outcome_event_ref VARBINARY(384) NOT NULL,
    idempotency_key VARBINARY(384) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    run_ref VARBINARY(384) NOT NULL,
    source_group_ref VARBINARY(384) NOT NULL,
    source_event_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL DEFAULT '',
    delivery_receipt_refs JSON NOT NULL,
    related_source_event_refs JSON NOT NULL,
    PRIMARY KEY (tenant_ref, outcome_event_ref),
    UNIQUE KEY outcome_events_ref_uq (outcome_event_ref),
    UNIQUE KEY outcome_events_idempotency_uq (tenant_ref, idempotency_key),
    KEY outcome_events_run_idx (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, run_ref, source_group_ref
    ),
    CONSTRAINT outcome_events_source_fk FOREIGN KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_event_ref
    ) REFERENCES source_events (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref
    ),
    CHECK (outcome_event_ref <> '' AND idempotency_key <> ''),
    CHECK (agent_ref <> '' AND run_ref <> '' AND source_group_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS seed_outcome_basis_links (
    tenant_ref VARBINARY(384) NOT NULL,
    seed_version_ref VARBINARY(384) NOT NULL,
    outcome_event_ref VARBINARY(384) NOT NULL,
    role VARBINARY(32) NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref, outcome_event_ref, role),
    KEY seed_outcome_basis_links_outcome_idx (tenant_ref, outcome_event_ref, seed_version_ref),
    CONSTRAINT seed_outcome_basis_version_fk FOREIGN KEY (tenant_ref, seed_version_ref)
        REFERENCES seed_versions (tenant_ref, seed_version_ref),
    CONSTRAINT seed_outcome_basis_outcome_fk FOREIGN KEY (tenant_ref, outcome_event_ref)
        REFERENCES outcome_events (tenant_ref, outcome_event_ref),
    CONSTRAINT seed_outcome_basis_links_role_check
        CHECK (role IN ('formation', 'revision', 'inhibition'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS consolidation_jobs (
    job_order BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_ref VARBINARY(384) NOT NULL,
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    episode_refs JSON NOT NULL,
    outcome_event_refs JSON NOT NULL,
    window_hash BINARY(32) NULL,
    not_before DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    lease_token VARBINARY(384) NOT NULL DEFAULT '',
    lease_until DATETIME(6) NULL,
    attempts INT NOT NULL DEFAULT 0,
    completed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    collecting_guard TINYINT GENERATED ALWAYS AS (
        CASE WHEN window_hash IS NULL AND completed_at IS NULL THEN 1 ELSE NULL END
    ) STORED,
    PRIMARY KEY (job_ref),
    UNIQUE KEY consolidation_jobs_order_uq (job_order),
    UNIQUE KEY consolidation_jobs_collecting_owner_uq (
        tenant_ref, scope_kind, agent_ref, relationship_ref, collecting_guard
    ),
    KEY consolidation_jobs_ready_idx (completed_at, not_before, updated_at, created_at),
    KEY consolidation_jobs_completed_owner_idx (
        tenant_ref, scope_kind, agent_ref, relationship_ref, completed_at
    ),
    CHECK (job_ref <> '' AND tenant_ref <> '' AND agent_ref <> ''),
    CHECK (JSON_LENGTH(episode_refs) > 0),
    CHECK (attempts >= 0),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (
        (lease_token = '' AND lease_until IS NULL) OR
        (lease_token <> '' AND lease_until IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS recollections (
    tenant_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    recollection_ref VARBINARY(384) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_ref, recollection_ref),
    UNIQUE KEY recollections_ref_uq (recollection_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> '' AND recollection_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS recollection_versions (
    tenant_ref VARBINARY(384) NOT NULL,
    recollection_ref VARBINARY(384) NOT NULL,
    recollection_version_ref VARBINARY(384) NOT NULL,
    version_number INT NOT NULL,
    text LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    application_scope VARBINARY(32) NOT NULL,
    status VARBINARY(32) NOT NULL,
    origin_job_ref VARBINARY(384) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    active_guard TINYINT GENERATED ALWAYS AS (
        CASE WHEN status = 'active' THEN 1 ELSE NULL END
    ) STORED,
    PRIMARY KEY (tenant_ref, recollection_version_ref),
    UNIQUE KEY recollection_versions_ref_uq (recollection_version_ref),
    UNIQUE KEY recollection_versions_number_uq (tenant_ref, recollection_ref, version_number),
    UNIQUE KEY recollection_versions_one_active_uq (tenant_ref, recollection_ref, active_guard),
    KEY recollection_versions_origin_job_idx (tenant_ref, origin_job_ref, recollection_version_ref),
    CONSTRAINT recollection_versions_recollection_fk FOREIGN KEY (tenant_ref, recollection_ref)
        REFERENCES recollections (tenant_ref, recollection_ref),
    CHECK (version_number > 0),
    CHECK (OCTET_LENGTH(text) > 0),
    CHECK (application_scope IN ('self', 'other', 'relation', 'situation')),
    CHECK (status IN ('active', 'superseded')),
    CHECK (origin_job_ref <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS recollection_basis_links (
    tenant_ref VARBINARY(384) NOT NULL,
    recollection_version_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL,
    role VARBINARY(32) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (tenant_ref, recollection_version_ref, episode_ref, role),
    KEY recollection_basis_links_episode_idx (tenant_ref, episode_ref, recollection_version_ref),
    CONSTRAINT recollection_basis_version_fk FOREIGN KEY (tenant_ref, recollection_version_ref)
        REFERENCES recollection_versions (tenant_ref, recollection_version_ref),
    CONSTRAINT recollection_basis_episode_fk FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (role IN ('formation', 'support', 'revision'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS memory_contexts (
    tenant_ref VARBINARY(384) NOT NULL,
    context_ref VARBINARY(384) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    run_ref VARBINARY(384) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    policy_ref VARBINARY(384) NOT NULL,
    constitution_ref VARBINARY(384) NOT NULL DEFAULT '',
    constitution_text LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT (''),
    PRIMARY KEY (tenant_ref, context_ref),
    UNIQUE KEY memory_contexts_ref_uq (context_ref),
    UNIQUE KEY memory_contexts_run_uq (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, run_ref
    ),
    CHECK (tenant_ref <> '' AND agent_ref <> ''),
    CHECK (context_ref <> '' AND run_ref <> '' AND policy_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (
        (constitution_ref = '' AND OCTET_LENGTH(constitution_text) = 0) OR
        (constitution_ref <> '' AND OCTET_LENGTH(constitution_text) > 0)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS memory_context_items (
    tenant_ref VARBINARY(384) NOT NULL,
    context_ref VARBINARY(384) NOT NULL,
    item_kind VARBINARY(32) NOT NULL,
    memory_ref VARBINARY(384) NOT NULL,
    item_order INT NOT NULL,
    query_source_ref VARBINARY(384) NOT NULL,
    matched_memory_ref VARBINARY(384) NOT NULL,
    evidence_episode_ref VARBINARY(384) NOT NULL,
    evidence_source_ref VARBINARY(384) NOT NULL,
    PRIMARY KEY (tenant_ref, context_ref, item_kind, memory_ref),
    UNIQUE KEY memory_context_items_order_uq (tenant_ref, context_ref, item_order),
    UNIQUE KEY memory_context_items_memory_ref_uq (tenant_ref, context_ref, memory_ref),
    KEY memory_context_items_ref_idx (tenant_ref, item_kind, memory_ref, context_ref),
    CONSTRAINT memory_context_items_context_fk FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    CONSTRAINT memory_context_items_episode_fk FOREIGN KEY (tenant_ref, evidence_episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (item_kind IN ('recollection', 'disposition')),
    CHECK (memory_ref <> '' AND item_order >= 0),
    CHECK (query_source_ref <> ''),
    CHECK (matched_memory_ref <> ''),
    CHECK (evidence_episode_ref <> '' AND evidence_source_ref <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS memory_delivery_receipts (
    tenant_ref VARBINARY(384) NOT NULL,
    receipt_ref VARBINARY(384) NOT NULL,
    idempotency_key VARBINARY(384) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    scope_kind VARBINARY(32) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    session_ref VARBINARY(384) NOT NULL DEFAULT '',
    run_ref VARBINARY(384) NOT NULL,
    context_ref VARBINARY(384) NOT NULL,
    delivered_memory_refs JSON NOT NULL,
    PRIMARY KEY (tenant_ref, receipt_ref),
    UNIQUE KEY memory_delivery_receipts_ref_uq (receipt_ref),
    UNIQUE KEY memory_delivery_receipts_idempotency_uq (tenant_ref, idempotency_key),
    CONSTRAINT memory_delivery_receipts_context_fk FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    CHECK (receipt_ref <> '' AND idempotency_key <> ''),
    CHECK (agent_ref <> '' AND run_ref <> '' AND context_ref <> ''),
    CHECK (JSON_LENGTH(delivered_memory_refs) > 0),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS memory_index_operations (
    operation_id VARBINARY(384) NOT NULL,
    stream_ref VARBINARY(384) NOT NULL,
    stream_sequence BIGINT NOT NULL,
    operation_kind VARBINARY(32) NOT NULL,
    document_kind VARBINARY(32) NOT NULL,
    document_ref VARBINARY(384) NOT NULL,
    tenant_ref VARBINARY(384) NOT NULL,
    agent_ref VARBINARY(384) NOT NULL,
    relationship_ref VARBINARY(384) NOT NULL DEFAULT '',
    attempt_count INT NOT NULL DEFAULT 0,
    not_before DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    lease_token VARBINARY(384) NULL,
    lease_until DATETIME(6) NULL,
    last_error LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT (''),
    acknowledged_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (operation_id),
    UNIQUE KEY memory_index_operations_stream_uq (stream_ref, stream_sequence),
    KEY memory_index_operations_ready_idx (acknowledged_at, not_before, created_at, operation_id),
    KEY memory_index_operations_stream_unfinished_idx (stream_ref, acknowledged_at, stream_sequence),
    CHECK (operation_id <> '' AND stream_ref <> '' AND stream_sequence > 0),
    CHECK (operation_kind IN ('UPSERT', 'DELETE')),
    CHECK (document_kind IN ('EPISODE', 'RECOLLECTION', 'DISPOSITION')),
    CHECK (document_ref <> '' AND tenant_ref <> '' AND agent_ref <> ''),
    CHECK (attempt_count >= 0),
    CHECK (
        (lease_token IS NULL AND lease_until IS NULL) OR
        (lease_token IS NOT NULL AND lease_until IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
