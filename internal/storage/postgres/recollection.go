package postgres

import (
	"context"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/jackc/pgx/v5"
)

const (
	maxActiveRecollectionTargets = 64
	maxRecollectionQueryBytes    = 32 * 1024
)

type activeRecollection struct {
	Owner         ownerScope
	Recollection  string
	VersionRef    string
	VersionNumber int
	Text          string
	Application   string
	CreatedAt     time.Time
	BasisState    map[string]struct{}
}

func loadActiveRecollections(
	ctx context.Context,
	querier consolidationQuerier,
	owner ownerScope,
	episodes []episodeEvidence,
	indexedRefs []string,
) ([]activeRecollection, error) {
	rows, err := querier.Query(ctx, `
		SELECT
			recollection.recollection_ref,
			version.recollection_version_ref,
			version.version_number,
			version.text,
			version.application_scope,
			version.created_at
		FROM recollections AS recollection
		JOIN recollection_versions AS version
		  ON version.tenant_ref = recollection.tenant_ref
		 AND version.recollection_ref = recollection.recollection_ref
		WHERE recollection.tenant_ref = $1
		  AND recollection.scope_kind = $2
		  AND recollection.agent_ref = $3
		  AND recollection.relationship_ref = $4
		  AND version.status = 'active'
		ORDER BY version.recollection_version_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef)
	if err != nil {
		return nil, storageError("load active Recollections", err)
	}
	defer rows.Close()

	recollections := make([]activeRecollection, 0)
	for rows.Next() {
		var recollection activeRecollection
		if err := rows.Scan(
			&recollection.Recollection,
			&recollection.VersionRef,
			&recollection.VersionNumber,
			&recollection.Text,
			&recollection.Application,
			&recollection.CreatedAt,
		); err != nil {
			return nil, storageError("scan active Recollection", err)
		}
		recollection.Owner = owner
		recollection.BasisState = make(map[string]struct{})
		recollections = append(recollections, recollection)
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate active Recollections", err)
	}
	rows.Close()
	recollections = boundedActiveRecollections(recollections, episodes, indexedRefs)

	for index := range recollections {
		basisRows, err := querier.Query(ctx, `
			SELECT role, episode_ref
			FROM recollection_basis_links
			WHERE tenant_ref = $1 AND recollection_version_ref = $2
			ORDER BY role, episode_ref
		`, owner.TenantRef, recollections[index].VersionRef)
		if err != nil {
			return nil, storageError("load Recollection Basis state", err)
		}
		for basisRows.Next() {
			var role, episodeRef string
			if err := basisRows.Scan(&role, &episodeRef); err != nil {
				basisRows.Close()
				return nil, storageError("scan Recollection Basis state", err)
			}
			recollections[index].BasisState[role+"\x00"+episodeRef] = struct{}{}
		}
		if err := basisRows.Err(); err != nil {
			basisRows.Close()
			return nil, storageError("iterate Recollection Basis state", err)
		}
		basisRows.Close()
	}
	return recollections, nil
}

// boundedActiveRecollections keeps the semantic Worker request independent of
// catalogue size. Relevance is transient: it is neither persisted nor exposed
// as confidence, and Core still revalidates every chosen Target and Basis.
func boundedActiveRecollections(
	recollections []activeRecollection,
	episodes []episodeEvidence,
	indexedRefs []string,
) []activeRecollection {
	if len(recollections) == 0 {
		return nil
	}
	query := recollectionWindowQuery(episodes)
	ranker := selection.DefaultRanker()
	type ranked struct {
		recollection activeRecollection
		score        float64
	}
	rankedValues := make([]ranked, 0, len(recollections))
	for _, recollection := range recollections {
		rankedValues = append(rankedValues, ranked{
			recollection: recollection,
			score:        ranker.Score(query, recollection.Text),
		})
	}
	sort.Slice(rankedValues, func(left, right int) bool {
		if rankedValues[left].score != rankedValues[right].score {
			return rankedValues[left].score > rankedValues[right].score
		}
		if !rankedValues[left].recollection.CreatedAt.Equal(rankedValues[right].recollection.CreatedAt) {
			return rankedValues[left].recollection.CreatedAt.After(rankedValues[right].recollection.CreatedAt)
		}
		return rankedValues[left].recollection.VersionRef < rankedValues[right].recollection.VersionRef
	})
	if len(rankedValues) > maxActiveRecollectionTargets {
		rankedValues = rankedValues[:maxActiveRecollectionTargets]
	}
	result := make([]activeRecollection, 0, len(rankedValues)+len(indexedRefs))
	selected := make(map[string]struct{}, len(rankedValues)+len(indexedRefs))
	for _, value := range rankedValues {
		result = append(result, value.recollection)
		selected[value.recollection.VersionRef] = struct{}{}
	}
	byVersion := make(map[string]activeRecollection, len(recollections))
	for _, recollection := range recollections {
		byVersion[recollection.VersionRef] = recollection
	}
	for _, ref := range indexedRefs {
		if _, duplicate := selected[ref]; duplicate {
			continue
		}
		if recollection, exists := byVersion[ref]; exists {
			result = append(result, recollection)
			selected[ref] = struct{}{}
		}
	}
	sort.Slice(result, func(left, right int) bool {
		return result[left].VersionRef < result[right].VersionRef
	})
	return result
}

func recollectionWindowQuery(episodes []episodeEvidence) string {
	var query strings.Builder
	for _, episode := range episodes {
		for _, source := range episode.Sources {
			if source.Role != ledger.RoleSituation {
				continue
			}
			text := strings.TrimSpace(source.Text)
			if text == "" || query.Len() >= maxRecollectionQueryBytes {
				continue
			}
			remaining := maxRecollectionQueryBytes - query.Len()
			if query.Len() > 0 {
				if remaining == 0 {
					continue
				}
				query.WriteByte('\n')
				remaining--
			}
			if len(text) > remaining {
				cut := remaining
				for cut > 0 && !utf8.ValidString(text[:cut]) {
					cut--
				}
				text = text[:cut]
			}
			query.WriteString(text)
		}
	}
	return query.String()
}

func hashRecollectionState(recollections []activeRecollection) [32]byte {
	fields := []string{"recollection-state.v1"}
	for _, recollection := range recollections {
		fields = append(fields,
			string(recollection.Owner.Kind), recollection.Owner.TenantRef,
			recollection.Owner.AgentRef, recollection.Owner.RelationshipRef,
			recollection.Recollection, recollection.VersionRef,
			fmt.Sprint(recollection.VersionNumber), recollection.Text, recollection.Application,
		)
		fields = append(fields, sortedMapKeys(recollection.BasisState)...)
	}
	return hashFields(fields...)
}

func applyRecollectionFormation(
	ctx context.Context,
	tx pgx.Tx,
	owner ownerScope,
	jobRef string,
	change consolidation.Change,
	episodeByRef map[string]episodeEvidence,
) (string, bool, error) {
	text := strings.TrimSpace(change.Text)
	basisRefs, eligible := eligibleRecollectionBasis(change.BasisRefs, episodeByRef)
	if change.Target != consolidation.TargetNewRecollection || change.Operation != consolidation.ChangeText ||
		text == "" || !validRecollectionApplication(change.Application) || !eligible {
		return "", false, nil
	}
	application := strings.ToLower(change.Application)

	var duplicate bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM recollections AS recollection
			JOIN recollection_versions AS version
			  ON version.tenant_ref = recollection.tenant_ref
			 AND version.recollection_ref = recollection.recollection_ref
			WHERE recollection.tenant_ref = $1
			  AND recollection.scope_kind = $2
			  AND recollection.agent_ref = $3
			  AND recollection.relationship_ref = $4
			  AND version.status = 'active'
			  AND version.application_scope = $5
			  AND version.text = $6
		)
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, application, text).Scan(&duplicate); err != nil {
		return "", false, storageError("check exact active Recollection duplicate", err)
	}
	if duplicate {
		return "", false, nil
	}

	recollectionRef, versionRef := recollectionFormationRefs(owner, jobRef, application, text, basisRefs)
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollections (
			tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref
		) VALUES ($1, $2, $3, $4, $5)
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, recollectionRef); err != nil {
		return "", false, storageError("insert Recollection", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, $4, $5, 'active', $6)
	`, owner.TenantRef, recollectionRef, versionRef, text, application, jobRef); err != nil {
		return "", false, storageError("insert RecollectionVersion v1", err)
	}
	if _, err := insertRecollectionBasis(ctx, tx, owner.TenantRef, versionRef, basisRefs, "formation"); err != nil {
		return "", false, err
	}
	return versionRef, true, nil
}

func applyRecollectionKeep(ctx context.Context, tx pgx.Tx, target *activeRecollection, refs []string) (bool, error) {
	return insertRecollectionBasis(ctx, tx, target.Owner.TenantRef, target.VersionRef, refs, "support")
}

func applyRecollectionRevision(
	ctx context.Context,
	tx pgx.Tx,
	jobRef string,
	target *activeRecollection,
	change consolidation.Change,
) (string, bool, error) {
	text := strings.TrimSpace(change.Text)
	if text == "" || text == target.Text {
		return "", false, nil
	}
	var duplicate bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM recollections AS recollection
			JOIN recollection_versions AS version
			  ON version.tenant_ref = recollection.tenant_ref
			 AND version.recollection_ref = recollection.recollection_ref
			WHERE recollection.tenant_ref = $1
			  AND recollection.scope_kind = $2
			  AND recollection.agent_ref = $3
			  AND recollection.relationship_ref = $4
			  AND version.status = 'active'
			  AND version.recollection_version_ref <> $5
			  AND version.application_scope = $6
			  AND version.text = $7
		)
	`,
		target.Owner.TenantRef, target.Owner.Kind, target.Owner.AgentRef,
		target.Owner.RelationshipRef, target.VersionRef, target.Application, text,
	).Scan(&duplicate); err != nil {
		return "", false, storageError("check exact active Recollection revision duplicate", err)
	}
	if duplicate {
		return "", false, nil
	}

	newVersionNumber := target.VersionNumber + 1
	newVersionRef := fmt.Sprintf("%s@%d", target.Recollection, newVersionNumber)
	tag, err := tx.Exec(ctx, `
		UPDATE recollection_versions
		SET status = 'superseded'
		WHERE tenant_ref = $1 AND recollection_version_ref = $2 AND status = 'active'
	`, target.Owner.TenantRef, target.VersionRef)
	if err != nil {
		return "", false, storageError("supersede revised RecollectionVersion", err)
	}
	if tag.RowsAffected() != 1 {
		return "", false, consolidation.ErrWindowStale
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, $4, $5, $6, 'active', $7)
	`,
		target.Owner.TenantRef, target.Recollection, newVersionRef,
		newVersionNumber, text, target.Application, jobRef,
	); err != nil {
		return "", false, storageError("insert revised RecollectionVersion", err)
	}
	if _, err := insertRecollectionBasis(
		ctx, tx, target.Owner.TenantRef, newVersionRef, change.BasisRefs, "revision",
	); err != nil {
		return "", false, err
	}
	return newVersionRef, true, nil
}

func insertRecollectionBasis(
	ctx context.Context,
	tx pgx.Tx,
	tenantRef, versionRef string,
	refs []string,
	role string,
) (bool, error) {
	applied := false
	for _, ref := range sortedRefs(refs) {
		tag, err := tx.Exec(ctx, `
			INSERT INTO recollection_basis_links (
				tenant_ref, recollection_version_ref, episode_ref, role
			) VALUES ($1, $2, $3, $4)
			ON CONFLICT DO NOTHING
		`, tenantRef, versionRef, ref, role)
		if err != nil {
			return false, storageError("append Recollection Basis", err)
		}
		applied = applied || tag.RowsAffected() == 1
	}
	return applied, nil
}

func eligibleRecollectionBasis(refs []string, episodeByRef map[string]episodeEvidence) ([]string, bool) {
	if len(refs) == 0 || !uniqueStableRefs(refs) {
		return nil, false
	}
	result := sortedRefs(refs)
	for _, ref := range result {
		if _, exists := episodeByRef[ref]; !exists {
			return nil, false
		}
	}
	return result, true
}

func recollectionFormationRefs(owner ownerScope, jobRef, application, text string, basisRefs []string) (string, string) {
	fields := []string{
		"recollection-form.v1", string(owner.Kind), owner.TenantRef, owner.AgentRef,
		owner.RelationshipRef, jobRef, application, text,
	}
	fields = append(fields, basisRefs...)
	hash := hashFields(fields...)
	recollectionRef := "recollection_" + hex.EncodeToString(hash[:16])
	return recollectionRef, recollectionRef + "@1"
}

func validConsolidationChanges(
	changes []consolidation.Change,
	windowOwner ownerScope,
	episodeByRef map[string]episodeEvidence,
	currentEpisodeRefs map[string]struct{},
	recollections map[string]*activeRecollection,
	dispositions map[string]*feedbackTarget,
	outcomes map[string]feedbackOutcome,
	evidence *consolidation.WindowEvidence,
) bool {
	counts := make(map[string]int, len(changes))
	for _, change := range changes {
		if change.Target != consolidation.TargetNewRecollection && change.Target != consolidation.TargetNewDisposition {
			counts[change.Target]++
		}
	}
	for _, change := range changes {
		isNew := change.Target == consolidation.TargetNewRecollection || change.Target == consolidation.TargetNewDisposition
		if (!isNew && counts[change.Target] != 1) || !uniqueStableRefs(change.BasisRefs) {
			return false
		}
		switch {
		case change.Target == consolidation.TargetNewRecollection:
			_, eligible := eligibleRecollectionBasis(change.BasisRefs, episodeByRef)
			if change.Operation != consolidation.ChangeText || strings.TrimSpace(change.Text) == "" ||
				!validRecollectionApplication(change.Application) || !eligible ||
				!basisIncludesCurrentEpisode(change.BasisRefs, currentEpisodeRefs) {
				return false
			}
		case change.Target == consolidation.TargetNewDisposition:
			_, _, eligible := eligibleDispositionFormationBasis(change.BasisRefs, episodeByRef, outcomes)
			if change.Operation == consolidation.ChangeAdapt {
				_, eligible = eligibleAdaptationBasis(change.BasisRefs, episodeByRef, currentEpisodeRefs)
			} else if consolidation.HasDispositionFormationAnchor(evidence) {
				eligible = eligible && exactDispositionFormationBasis(evidence, change.BasisRefs)
			}
			if (change.Operation != consolidation.ChangeText && change.Operation != consolidation.ChangeAdapt) || strings.TrimSpace(change.Text) == "" ||
				change.Application != dispositionApplication(windowOwner) || !eligible ||
				!basisIncludesCurrentEpisode(change.BasisRefs, currentEpisodeRefs) {
				return false
			}
		case recollections[change.Target] != nil:
			target := recollections[change.Target]
			_, eligible := eligibleRecollectionBasis(change.BasisRefs, episodeByRef)
			if change.Application != strings.ToUpper(target.Application) || !eligible ||
				!basisIncludesCurrentEpisode(change.BasisRefs, currentEpisodeRefs) ||
				(change.Operation != consolidation.ChangeKeep && change.Operation != consolidation.ChangeText) ||
				(change.Operation == consolidation.ChangeText && strings.TrimSpace(change.Text) == "") {
				return false
			}
		case dispositions[change.Target] != nil:
			target := dispositions[change.Target]
			if change.Application != dispositionApplication(target.Owner) {
				return false
			}
			switch change.Operation {
			case consolidation.ChangeAdapt:
				if len(change.BasisRefs) != 1 || target.Owner != windowOwner || strings.TrimSpace(change.Text) == "" ||
					!basisIncludesCurrentEpisode(change.BasisRefs, target.DirectEpisodes) {
					return false
				}
				// Only independently selected current/related evidence or
				// anchors offered for behavioral feedback may be cited. Hidden
				// catalogue history remains canonical state, not model authority.
				for _, ref := range change.BasisRefs {
					if _, exists := episodeByRef[ref]; !exists && (len(target.Episodes) == 0 || !hasRef(target.Anchors, ref)) {
						return false
					}
				}
			case consolidation.ChangeReenact:
				if len(change.BasisRefs) == 0 {
					return false
				}
				for _, ref := range change.BasisRefs {
					if !hasRef(target.Episodes, ref) {
						return false
					}
				}
			case consolidation.ChangeInhibit, consolidation.ChangeText:
				episodes, outcomes, valid := classifyFeedbackBasis(target, change.BasisRefs, change.Operation == consolidation.ChangeText)
				if !valid || len(episodes) == 0 || len(outcomes) == 0 ||
					!exactFeedbackEpisodeOutcomeMatch(target, episodes, outcomes) ||
					(change.Operation == consolidation.ChangeText && strings.TrimSpace(change.Text) == "") {
					return false
				}
			default:
				return false
			}
		default:
			return false
		}
	}
	return true
}

func basisIncludesCurrentEpisode(refs []string, current map[string]struct{}) bool {
	for _, ref := range refs {
		if _, exists := current[ref]; exists {
			return true
		}
	}
	return false
}

func dispositionApplication(owner ownerScope) string {
	if owner.Kind == ledger.ScopeKindAgent {
		return consolidation.ApplicationSelf
	}
	return consolidation.ApplicationRelation
}

func validRecollectionApplication(application string) bool {
	switch application {
	case consolidation.ApplicationSelf, consolidation.ApplicationOther,
		consolidation.ApplicationRelation, consolidation.ApplicationSituation:
		return true
	default:
		return false
	}
}

func consolidationLockOwners(windowOwner ownerScope, targets map[string]*feedbackTarget) []ownerScope {
	byKey := map[string]ownerScope{ownerSortKey(windowOwner): windowOwner}
	for _, target := range targets {
		byKey[ownerSortKey(target.Owner)] = target.Owner
	}
	keys := sortedMapKeys(byKey)
	owners := make([]ownerScope, 0, len(keys))
	for _, key := range keys {
		owners = append(owners, byKey[key])
	}
	return owners
}

func ownerSortKey(owner ownerScope) string {
	return string(owner.Kind) + "\x00" + owner.TenantRef + "\x00" + owner.AgentRef + "\x00" + owner.RelationshipRef
}

func sortOwnerScopes(owners []ownerScope) {
	sort.Slice(owners, func(left, right int) bool {
		return ownerSortKey(owners[left]) < ownerSortKey(owners[right])
	})
}
