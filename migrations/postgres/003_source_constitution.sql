-- Admission is DB-local first acceptance order, not historical event time.
-- Legacy rows remain unknown; backfilled ordinals do not invent a baseline.
ALTER TABLE source_events ADD COLUMN IF NOT EXISTS constitution_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE source_events ADD COLUMN IF NOT EXISTS constitution_text TEXT NOT NULL DEFAULT '';
ALTER TABLE source_events ADD COLUMN IF NOT EXISTS admission_order BIGSERIAL;
