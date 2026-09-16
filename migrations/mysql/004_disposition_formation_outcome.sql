-- Permit a non-Agent Outcome to remain an explicit causal Basis of the first
-- SeedVersion. Existing revision and inhibition roles are unchanged.
SET @seed_outcome_basis_check_name = (
    SELECT tc.constraint_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.check_constraints AS cc
      ON cc.constraint_schema = tc.constraint_schema
     AND cc.constraint_name = tc.constraint_name
    WHERE tc.constraint_schema = DATABASE()
      AND tc.table_name = 'seed_outcome_basis_links'
      AND tc.constraint_type = 'CHECK'
      AND LOWER(cc.check_clause) LIKE '%role%'
      AND LOWER(cc.check_clause) LIKE '%revision%'
      AND LOWER(cc.check_clause) LIKE '%inhibition%'
    LIMIT 1
);
SET @seed_outcome_basis_allows_formation = (
    SELECT COUNT(*)
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.check_constraints AS cc
      ON cc.constraint_schema = tc.constraint_schema
     AND cc.constraint_name = tc.constraint_name
    WHERE tc.constraint_schema = DATABASE()
      AND tc.table_name = 'seed_outcome_basis_links'
      AND tc.constraint_type = 'CHECK'
      AND LOWER(cc.check_clause) LIKE '%role%'
      AND LOWER(cc.check_clause) LIKE '%formation%'
);
SET @seed_outcome_basis_ddl = IF(
    @seed_outcome_basis_allows_formation > 0,
    'SELECT 1',
    IF(
        @seed_outcome_basis_check_name IS NULL,
        'ALTER TABLE seed_outcome_basis_links ADD CONSTRAINT seed_outcome_basis_links_role_check CHECK (role IN (''formation'', ''revision'', ''inhibition''))',
        CONCAT(
            'ALTER TABLE seed_outcome_basis_links DROP CHECK `',
            REPLACE(@seed_outcome_basis_check_name, '`', '``'),
            '`, ADD CONSTRAINT seed_outcome_basis_links_role_check CHECK (role IN (''formation'', ''revision'', ''inhibition''))'
        )
    )
);
PREPARE seed_outcome_basis_stmt FROM @seed_outcome_basis_ddl;
EXECUTE seed_outcome_basis_stmt;
DEALLOCATE PREPARE seed_outcome_basis_stmt;
