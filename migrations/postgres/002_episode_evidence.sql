-- Add the frozen projection for exact Episode evidence selected into a Context.
CREATE TABLE IF NOT EXISTS memory_context_episode_evidence (
    tenant_ref text NOT NULL,
    context_ref text NOT NULL,
    episode_ref text NOT NULL,
    evidence_order integer NOT NULL,
    query_source_ref text NOT NULL,
    evidence_text text NOT NULL,
    PRIMARY KEY (tenant_ref, context_ref, episode_ref),
    UNIQUE (tenant_ref, context_ref, evidence_order),
    FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (episode_ref <> '' AND query_source_ref <> ''),
    CHECK (evidence_order >= 0 AND octet_length(evidence_text) > 0)
);

CREATE INDEX IF NOT EXISTS memory_context_episode_evidence_ref_idx
ON memory_context_episode_evidence (tenant_ref, episode_ref, context_ref);
