-- Memory Core PostgreSQL V1: complete fresh-install causal schema.
-- Historical PersonaContext/SeedActivation delivery tables are intentionally absent.

CREATE TABLE IF NOT EXISTS source_events (
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    source_ref text NOT NULL,
    actor_kind text NOT NULL,
    actor_ref text NOT NULL,
    source_text text NOT NULL,
    PRIMARY KEY (
        tenant_ref,
        scope_kind,
        agent_ref,
        relationship_ref,
        session_ref,
        source_ref
    ),
    CHECK (tenant_ref <> '' AND agent_ref <> '' AND source_ref <> ''),
    CHECK (source_text <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (actor_kind IN ('agent', 'user', 'system', 'tool', 'external')),
    CHECK (actor_ref <> '')
);

CREATE TABLE IF NOT EXISTS episodes (
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    run_ref text NOT NULL,
    source_group_ref text NOT NULL,
    episode_ref text NOT NULL UNIQUE,
    PRIMARY KEY (
        tenant_ref,
        scope_kind,
        agent_ref,
        relationship_ref,
        session_ref,
        run_ref,
        source_group_ref
    ),
    CHECK (tenant_ref <> '' AND agent_ref <> ''),
    CHECK (run_ref <> '' AND source_group_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE TABLE IF NOT EXISTS episode_links (
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    run_ref text NOT NULL,
    source_group_ref text NOT NULL,
    source_event_ref text NOT NULL,
    role text NOT NULL,
    episode_ref text NOT NULL DEFAULT '',
    PRIMARY KEY (
        tenant_ref,
        scope_kind,
        agent_ref,
        relationship_ref,
        session_ref,
        run_ref,
        source_group_ref,
        source_event_ref,
        role
    ),
    FOREIGN KEY (
        tenant_ref,
        scope_kind,
        agent_ref,
        relationship_ref,
        session_ref,
        source_event_ref
    ) REFERENCES source_events (
        tenant_ref,
        scope_kind,
        agent_ref,
        relationship_ref,
        session_ref,
        source_ref
    ),
    CHECK (run_ref <> '' AND source_group_ref <> ''),
    CHECK (role IN ('situation', 'agent_act', 'outcome'))
);

CREATE INDEX IF NOT EXISTS episode_links_group_gate_idx ON episode_links (
    tenant_ref,
    scope_kind,
    agent_ref,
    relationship_ref,
    session_ref,
    run_ref,
    source_group_ref,
    role,
    source_event_ref
);

CREATE INDEX IF NOT EXISTS episode_links_episode_ref_idx ON episode_links (episode_ref)
WHERE episode_ref <> '';

CREATE TABLE IF NOT EXISTS request_receipts (
    tenant_ref text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash bytea NOT NULL,
    source_event_ref text NOT NULL,
    episode_ref text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_ref, idempotency_key),
    CHECK (tenant_ref <> '' AND idempotency_key <> ''),
    CHECK (octet_length(request_hash) = 32),
    CHECK (source_event_ref <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS episodes_tenant_episode_ref_uq
ON episodes (tenant_ref, episode_ref);

CREATE TABLE IF NOT EXISTS disposition_seeds (
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    seed_ref text NOT NULL,
    PRIMARY KEY (tenant_ref, seed_ref),
    UNIQUE (seed_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> '' AND seed_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE TABLE IF NOT EXISTS seed_versions (
    tenant_ref text NOT NULL,
    seed_ref text NOT NULL,
    seed_version_ref text NOT NULL,
    version_number integer NOT NULL,
    tendency_text text NOT NULL,
    status text NOT NULL,
    origin_job_ref text NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref),
    UNIQUE (seed_version_ref),
    UNIQUE (tenant_ref, seed_ref, version_number),
    FOREIGN KEY (tenant_ref, seed_ref)
        REFERENCES disposition_seeds (tenant_ref, seed_ref),
    CHECK (version_number > 0),
    CHECK (tendency_text <> ''),
    CHECK (status IN ('active', 'superseded', 'ineligible')),
    CHECK (origin_job_ref <> '')
);

CREATE INDEX IF NOT EXISTS seed_versions_origin_job_idx
ON seed_versions (tenant_ref, origin_job_ref, seed_version_ref);

CREATE TABLE IF NOT EXISTS seed_basis_links (
    tenant_ref text NOT NULL,
    seed_version_ref text NOT NULL,
    episode_ref text NOT NULL,
    role text NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref, episode_ref, role),
    FOREIGN KEY (tenant_ref, seed_version_ref)
        REFERENCES seed_versions (tenant_ref, seed_version_ref),
    FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (role IN ('formation', 'revision', 'reenactment', 'inhibition'))
);

CREATE INDEX IF NOT EXISTS seed_basis_links_episode_idx
ON seed_basis_links (tenant_ref, episode_ref, seed_version_ref);

CREATE TABLE IF NOT EXISTS consolidation_receipts (
    tenant_ref text NOT NULL,
    job_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    request_hash bytea NOT NULL,
    PRIMARY KEY (tenant_ref, job_ref),
    CHECK (tenant_ref <> '' AND job_ref <> '' AND agent_ref <> ''),
    CHECK (octet_length(request_hash) = 32),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE TABLE IF NOT EXISTS outcome_events (
    tenant_ref text NOT NULL,
    outcome_event_ref text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash bytea NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    run_ref text NOT NULL,
    source_group_ref text NOT NULL,
    source_event_ref text NOT NULL,
    episode_ref text NOT NULL DEFAULT '',
    delivery_receipt_refs text[] NOT NULL,
    related_source_event_refs text[] NOT NULL,
    PRIMARY KEY (tenant_ref, outcome_event_ref),
    UNIQUE (outcome_event_ref),
    UNIQUE (tenant_ref, idempotency_key),
    FOREIGN KEY (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_event_ref
    ) REFERENCES source_events (
        tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref
    ),
    CHECK (outcome_event_ref <> '' AND idempotency_key <> ''),
    CHECK (octet_length(request_hash) = 32),
    CHECK (agent_ref <> '' AND run_ref <> '' AND source_group_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE INDEX IF NOT EXISTS outcome_events_run_idx ON outcome_events (
    tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, run_ref, source_group_ref
);

CREATE TABLE IF NOT EXISTS seed_outcome_basis_links (
    tenant_ref text NOT NULL,
    seed_version_ref text NOT NULL,
    outcome_event_ref text NOT NULL,
    role text NOT NULL,
    PRIMARY KEY (tenant_ref, seed_version_ref, outcome_event_ref, role),
    FOREIGN KEY (tenant_ref, seed_version_ref)
        REFERENCES seed_versions (tenant_ref, seed_version_ref),
    FOREIGN KEY (tenant_ref, outcome_event_ref)
        REFERENCES outcome_events (tenant_ref, outcome_event_ref),
    CONSTRAINT seed_outcome_basis_links_role_check
        CHECK (role IN ('formation', 'revision', 'inhibition'))
);

CREATE INDEX IF NOT EXISTS seed_outcome_basis_links_outcome_idx
ON seed_outcome_basis_links (tenant_ref, outcome_event_ref, seed_version_ref);

CREATE TABLE IF NOT EXISTS consolidation_jobs (
    job_order bigserial NOT NULL UNIQUE,
    job_ref text PRIMARY KEY,
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    episode_refs text[] NOT NULL,
    outcome_event_refs text[] NOT NULL DEFAULT '{}',
    window_hash bytea,
    not_before timestamptz NOT NULL DEFAULT now(),
    lease_token text NOT NULL DEFAULT '',
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (job_ref <> '' AND tenant_ref <> '' AND agent_ref <> ''),
    CHECK (cardinality(episode_refs) > 0),
    CHECK (window_hash IS NULL OR octet_length(window_hash) = 32),
    CHECK (attempts >= 0),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (
        (lease_token = '' AND lease_until IS NULL) OR
        (lease_token <> '' AND lease_until IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS consolidation_jobs_collecting_owner_uq
ON consolidation_jobs (tenant_ref, scope_kind, agent_ref, relationship_ref)
WHERE window_hash IS NULL AND completed_at IS NULL;

CREATE INDEX IF NOT EXISTS consolidation_jobs_ready_idx
ON consolidation_jobs (not_before, updated_at, created_at)
WHERE completed_at IS NULL;

CREATE INDEX IF NOT EXISTS consolidation_jobs_completed_owner_idx
ON consolidation_jobs (
    tenant_ref, scope_kind, agent_ref, relationship_ref, completed_at DESC
)
WHERE completed_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS recollections (
    tenant_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    recollection_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_ref, recollection_ref),
    UNIQUE (recollection_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> '' AND recollection_ref <> ''),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE TABLE IF NOT EXISTS recollection_versions (
    tenant_ref text NOT NULL,
    recollection_ref text NOT NULL,
    recollection_version_ref text NOT NULL,
    version_number integer NOT NULL,
    text text NOT NULL,
    application_scope text NOT NULL,
    status text NOT NULL,
    origin_job_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_ref, recollection_version_ref),
    UNIQUE (recollection_version_ref),
    UNIQUE (tenant_ref, recollection_ref, version_number),
    FOREIGN KEY (tenant_ref, recollection_ref)
        REFERENCES recollections (tenant_ref, recollection_ref),
    CHECK (version_number > 0),
    CHECK (text <> ''),
    CHECK (application_scope IN ('self', 'other', 'relation', 'situation')),
    CHECK (status IN ('active', 'superseded')),
    CHECK (origin_job_ref <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS recollection_versions_one_active_uq
ON recollection_versions (tenant_ref, recollection_ref)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS recollection_versions_origin_job_idx
ON recollection_versions (tenant_ref, origin_job_ref, recollection_version_ref);

CREATE TABLE IF NOT EXISTS recollection_basis_links (
    tenant_ref text NOT NULL,
    recollection_version_ref text NOT NULL,
    episode_ref text NOT NULL,
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_ref, recollection_version_ref, episode_ref, role),
    FOREIGN KEY (tenant_ref, recollection_version_ref)
        REFERENCES recollection_versions (tenant_ref, recollection_version_ref),
    FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (role IN ('formation', 'support', 'revision'))
);

CREATE INDEX IF NOT EXISTS recollection_basis_links_episode_idx
ON recollection_basis_links (tenant_ref, episode_ref, recollection_version_ref);

CREATE TABLE IF NOT EXISTS memory_contexts (
    tenant_ref text NOT NULL,
    context_ref text NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    run_ref text NOT NULL,
    request_hash bytea NOT NULL,
    policy_ref text NOT NULL,
    constitution_ref text NOT NULL DEFAULT '',
    constitution_text text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_ref, context_ref),
    UNIQUE (context_ref),
    UNIQUE (tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, run_ref),
    CHECK (tenant_ref <> '' AND agent_ref <> ''),
    CHECK (context_ref <> '' AND run_ref <> '' AND policy_ref <> ''),
    CHECK (octet_length(request_hash) = 32),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    ),
    CHECK (
        (constitution_ref = '' AND constitution_text = '') OR
        (constitution_ref <> '' AND constitution_text <> '')
    )
);

CREATE TABLE IF NOT EXISTS memory_context_items (
    tenant_ref text NOT NULL,
    context_ref text NOT NULL,
    item_kind text NOT NULL,
    memory_ref text NOT NULL,
    item_order integer NOT NULL,
    query_source_ref text NOT NULL,
    matched_memory_ref text NOT NULL,
    evidence_episode_ref text NOT NULL,
    evidence_source_ref text NOT NULL,
    PRIMARY KEY (tenant_ref, context_ref, item_kind, memory_ref),
    UNIQUE (tenant_ref, context_ref, item_order),
    FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    FOREIGN KEY (tenant_ref, evidence_episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (item_kind IN ('recollection', 'disposition')),
    CHECK (memory_ref <> '' AND item_order >= 0),
    CHECK (query_source_ref <> ''),
    CHECK (matched_memory_ref <> ''),
    CHECK (evidence_episode_ref <> '' AND evidence_source_ref <> '')
);

CREATE INDEX IF NOT EXISTS memory_context_items_ref_idx
ON memory_context_items (tenant_ref, item_kind, memory_ref, context_ref);

CREATE UNIQUE INDEX IF NOT EXISTS memory_context_items_memory_ref_uq
ON memory_context_items (tenant_ref, context_ref, memory_ref);

CREATE TABLE IF NOT EXISTS memory_delivery_receipts (
    tenant_ref text NOT NULL,
    receipt_ref text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash bytea NOT NULL,
    scope_kind text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    session_ref text NOT NULL DEFAULT '',
    run_ref text NOT NULL,
    context_ref text NOT NULL,
    delivered_memory_refs text[] NOT NULL,
    PRIMARY KEY (tenant_ref, receipt_ref),
    UNIQUE (receipt_ref),
    UNIQUE (tenant_ref, idempotency_key),
    FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    CHECK (receipt_ref <> '' AND idempotency_key <> ''),
    CHECK (octet_length(request_hash) = 32),
    CHECK (agent_ref <> '' AND run_ref <> '' AND context_ref <> ''),
    CHECK (cardinality(delivered_memory_refs) > 0),
    CHECK (
        (scope_kind = 'agent' AND relationship_ref = '') OR
        (scope_kind = 'relationship' AND relationship_ref <> '')
    )
);

CREATE TABLE IF NOT EXISTS memory_index_operations (
    operation_id text PRIMARY KEY,
    stream_ref text NOT NULL,
    stream_sequence bigint NOT NULL,
    operation_kind text NOT NULL,
    document_kind text NOT NULL,
    document_ref text NOT NULL,
    tenant_ref text NOT NULL,
    agent_ref text NOT NULL,
    relationship_ref text NOT NULL DEFAULT '',
    attempt_count integer NOT NULL DEFAULT 0,
    not_before timestamptz NOT NULL DEFAULT now(),
    lease_token text,
    lease_until timestamptz,
    last_error text NOT NULL DEFAULT '',
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (stream_ref, stream_sequence),
    CHECK (operation_id <> '' AND stream_ref <> '' AND stream_sequence > 0),
    CHECK (operation_kind IN ('UPSERT', 'DELETE')),
    CHECK (document_kind IN ('EPISODE', 'RECOLLECTION', 'DISPOSITION')),
    CHECK (document_ref <> '' AND tenant_ref <> '' AND agent_ref <> ''),
    CHECK (attempt_count >= 0),
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);

CREATE INDEX IF NOT EXISTS memory_index_operations_ready_idx
ON memory_index_operations (not_before, created_at, operation_id)
WHERE acknowledged_at IS NULL;

CREATE INDEX IF NOT EXISTS memory_index_operations_stream_unfinished_idx
ON memory_index_operations (stream_ref, stream_sequence)
WHERE acknowledged_at IS NULL;
