-- Permit a non-Agent Outcome to remain an explicit causal Basis of the first
-- SeedVersion. Existing revision and inhibition roles are unchanged.
DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'seed_outcome_basis_links'::regclass
          AND conname = 'seed_outcome_basis_links_role_check'
          AND position('formation' IN pg_get_constraintdef(oid)) = 0
    ) THEN
        ALTER TABLE seed_outcome_basis_links
            DROP CONSTRAINT seed_outcome_basis_links_role_check;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'seed_outcome_basis_links'::regclass
          AND conname = 'seed_outcome_basis_links_role_check'
    ) THEN
        ALTER TABLE seed_outcome_basis_links
            ADD CONSTRAINT seed_outcome_basis_links_role_check
            CHECK (role IN ('formation', 'revision', 'inhibition'));
    END IF;
END
$migration$;
