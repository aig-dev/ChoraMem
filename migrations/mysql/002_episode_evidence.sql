-- Add the frozen projection for exact Episode evidence selected into a Context.
CREATE TABLE IF NOT EXISTS memory_context_episode_evidence (
    tenant_ref VARBINARY(384) NOT NULL,
    context_ref VARBINARY(384) NOT NULL,
    episode_ref VARBINARY(384) NOT NULL,
    evidence_order INT NOT NULL,
    query_source_ref VARBINARY(384) NOT NULL,
    evidence_text LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    PRIMARY KEY (tenant_ref, context_ref, episode_ref),
    UNIQUE KEY memory_context_episode_evidence_order_uq (tenant_ref, context_ref, evidence_order),
    KEY memory_context_episode_evidence_ref_idx (tenant_ref, episode_ref, context_ref),
    CONSTRAINT memory_context_episode_evidence_context_fk FOREIGN KEY (tenant_ref, context_ref)
        REFERENCES memory_contexts (tenant_ref, context_ref),
    CONSTRAINT memory_context_episode_evidence_episode_fk FOREIGN KEY (tenant_ref, episode_ref)
        REFERENCES episodes (tenant_ref, episode_ref),
    CHECK (episode_ref <> '' AND query_source_ref <> ''),
    CHECK (evidence_order >= 0 AND OCTET_LENGTH(evidence_text) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
