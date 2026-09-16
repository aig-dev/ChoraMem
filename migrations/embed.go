// Package migrations embeds the complete fresh-install V1 schema for each
// supported relational adapter.
package migrations

import _ "embed"

// PostgresV1SQL is the single clean PostgreSQL initialization schema.
//
//go:embed postgres/001_v1.sql
var PostgresV1SQL string

// PostgresEpisodeEvidenceSQL adds the bounded Context evidence projection.
//
//go:embed postgres/002_episode_evidence.sql
var PostgresEpisodeEvidenceSQL string

// MySQLV1SQL is the single clean MySQL 8 initialization schema.
//
//go:embed mysql/001_v1.sql
var MySQLV1SQL string

// MySQLEpisodeEvidenceSQL adds the bounded Context evidence projection.
//
//go:embed mysql/002_episode_evidence.sql
var MySQLEpisodeEvidenceSQL string

//go:embed postgres/003_source_constitution.sql
var PostgresSourceConstitutionSQL string

//go:embed mysql/003_source_constitution.sql
var MySQLSourceConstitutionSQL string

// PostgresDispositionFormationOutcomeSQL permits Outcome provenance on a new Seed.
//
//go:embed postgres/004_disposition_formation_outcome.sql
var PostgresDispositionFormationOutcomeSQL string

// MySQLDispositionFormationOutcomeSQL permits Outcome provenance on a new Seed.
//
//go:embed mysql/004_disposition_formation_outcome.sql
var MySQLDispositionFormationOutcomeSQL string
