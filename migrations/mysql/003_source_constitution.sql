-- MySQL 8 has no ADD COLUMN IF NOT EXISTS. Each independently guarded ALTER
-- can resume after DDL auto-commit; Store holds the existing migration lock.
SET @source_constitution_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'source_events' AND column_name = 'constitution_ref') = 0, 'ALTER TABLE source_events ADD COLUMN constitution_ref TEXT NOT NULL DEFAULT ('''')', 'SELECT 1');
PREPARE source_constitution_stmt FROM @source_constitution_ddl;
EXECUTE source_constitution_stmt;
DEALLOCATE PREPARE source_constitution_stmt;
SET @source_constitution_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'source_events' AND column_name = 'constitution_text') = 0, 'ALTER TABLE source_events ADD COLUMN constitution_text MEDIUMTEXT NOT NULL DEFAULT ('''')', 'SELECT 1');
PREPARE source_constitution_stmt FROM @source_constitution_ddl;
EXECUTE source_constitution_stmt;
DEALLOCATE PREPARE source_constitution_stmt;
SET @source_constitution_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'source_events' AND column_name = 'admission_order') = 0, 'ALTER TABLE source_events ADD COLUMN admission_order BIGINT NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY source_admission_order (admission_order)', 'SELECT 1');
PREPARE source_constitution_stmt FROM @source_constitution_ddl;
EXECUTE source_constitution_stmt;
DEALLOCATE PREPARE source_constitution_stmt;
