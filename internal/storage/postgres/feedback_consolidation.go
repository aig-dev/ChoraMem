package postgres

import (
	"context"
	"crypto/sha256"
	"fmt"
	"sort"
	"strings"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/jackc/pgx/v5"
)

type feedbackTarget struct {
	Owner           ownerScope
	SeedRef         string
	VersionRef      string
	VersionNumber   int
	Tendency        string
	Episodes        map[string]struct{}
	DirectEpisodes  map[string]struct{}
	Receipts        map[string]map[string]struct{}
	Outcomes        map[string]struct{}
	OutcomeEpisodes map[string]string
	Anchors         map[string]struct{}
	AnchorEvidence  map[string]episodeEvidence
	BasisState      map[string]struct{}
}

type feedbackOutcome struct {
	Ref          string
	EpisodeRef   string
	Text         string
	ActorKind    ledger.ActorKind
	ActorRef     string
	DeliveryRefs []string
}

func loadFeedbackState(ctx context.Context, querier consolidationQuerier, episodes []episodeEvidence, outcomeRefs []string, activeDispositions []activeDisposition) (map[string]*feedbackTarget, map[string]feedbackOutcome, error) {
	targets := make(map[string]*feedbackTarget)
	for _, episode := range episodes {
		rows, err := querier.Query(ctx, `
			SELECT DISTINCT
				seed.scope_kind, seed.tenant_ref, seed.agent_ref, seed.relationship_ref,
				version.seed_ref, version.seed_version_ref, version.version_number,
				version.tendency_text, delivery.receipt_ref
			FROM memory_contexts AS memory_context
			JOIN memory_context_items AS memory_item
			  ON memory_item.tenant_ref = memory_context.tenant_ref
			 AND memory_item.context_ref = memory_context.context_ref
			 AND memory_item.item_kind = 'disposition'
			JOIN memory_delivery_receipts AS delivery
			  ON delivery.tenant_ref = memory_context.tenant_ref
			 AND delivery.context_ref = memory_context.context_ref
			 AND delivery.run_ref = memory_context.run_ref
			 AND memory_item.memory_ref = ANY(delivery.delivered_memory_refs)
			JOIN seed_versions AS version
			  ON version.tenant_ref = memory_item.tenant_ref
			 AND version.seed_version_ref = memory_item.memory_ref
			 AND version.status = 'active'
			JOIN disposition_seeds AS seed
			  ON seed.tenant_ref = version.tenant_ref
			 AND seed.seed_ref = version.seed_ref
			WHERE memory_context.tenant_ref = $1
			  AND memory_context.scope_kind = $2
			  AND memory_context.agent_ref = $3
			  AND memory_context.relationship_ref = $4
			  AND memory_context.session_ref = $5
			  AND memory_context.run_ref = $6
			  AND EXISTS (
				SELECT 1 FROM episode_links AS link
				WHERE link.tenant_ref = memory_context.tenant_ref
				  AND link.scope_kind = memory_context.scope_kind
				  AND link.agent_ref = memory_context.agent_ref
				  AND link.relationship_ref = memory_context.relationship_ref
				  AND link.session_ref = memory_context.session_ref
				  AND link.run_ref = memory_context.run_ref
				  AND link.source_group_ref = $7
				  AND link.episode_ref = $8
				  AND link.role = 'situation'
				  AND link.source_event_ref = memory_item.query_source_ref
			  )
			ORDER BY version.seed_version_ref, delivery.receipt_ref
		`,
			episode.Owner.TenantRef,
			episode.Owner.Kind,
			episode.Owner.AgentRef,
			episode.Owner.RelationshipRef,
			episode.SessionRef,
			episode.RunRef,
			episode.GroupRef,
			episode.Ref,
		)
		if err != nil {
			return nil, nil, storageError("load feedback Targets", err)
		}
		for rows.Next() {
			var targetOwner ownerScope
			var seedRef, versionRef, tendency, receiptRef string
			var versionNumber int
			if err := rows.Scan(
				&targetOwner.Kind, &targetOwner.TenantRef, &targetOwner.AgentRef, &targetOwner.RelationshipRef,
				&seedRef, &versionRef, &versionNumber, &tendency, &receiptRef,
			); err != nil {
				rows.Close()
				return nil, nil, storageError("scan feedback Target", err)
			}
			target := targets[versionRef]
			if target == nil {
				target = &feedbackTarget{
					Owner:   targetOwner,
					SeedRef: seedRef, VersionRef: versionRef, VersionNumber: versionNumber, Tendency: tendency,
					Episodes: make(map[string]struct{}), Receipts: make(map[string]map[string]struct{}),
					Outcomes: make(map[string]struct{}), OutcomeEpisodes: make(map[string]string), Anchors: make(map[string]struct{}),
					AnchorEvidence: make(map[string]episodeEvidence), BasisState: make(map[string]struct{}),
				}
				targets[versionRef] = target
			} else if target.Owner != targetOwner {
				rows.Close()
				return nil, nil, consolidation.ErrInvalidWindow
			}
			target.Episodes[episode.Ref] = struct{}{}
			if target.Receipts[episode.Ref] == nil {
				target.Receipts[episode.Ref] = make(map[string]struct{})
			}
			target.Receipts[episode.Ref][receiptRef] = struct{}{}
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, nil, storageError("iterate feedback Targets", err)
		}
		rows.Close()
	}

	// Direct requirement candidates never enter Episodes/Receipts/Outcomes:
	// those fields exclusively represent actual behavioral feedback.
	direct := make(map[string]struct{})
	for _, episode := range episodes {
		if episodeHasSituationActor(episode, true) {
			direct[episode.Ref] = struct{}{}
		}
	}
	if len(direct) > 0 {
		for _, disposition := range activeDispositions {
			target := targets[disposition.VersionRef]
			if target == nil {
				target = &feedbackTarget{
					Owner: disposition.Owner, SeedRef: disposition.SeedRef,
					VersionRef: disposition.VersionRef, VersionNumber: disposition.VersionNumber, Tendency: disposition.Tendency,
					Episodes: make(map[string]struct{}), Receipts: make(map[string]map[string]struct{}),
					Outcomes: make(map[string]struct{}), OutcomeEpisodes: make(map[string]string),
					Anchors: make(map[string]struct{}), AnchorEvidence: make(map[string]episodeEvidence), BasisState: make(map[string]struct{}),
				}
				targets[target.VersionRef] = target
			}
			if target.Owner == episodes[0].Owner {
				target.DirectEpisodes = direct
			}
		}
	}

	for _, target := range targets {
		rows, err := querier.Query(ctx, `
			SELECT basis.role, basis.episode_ref
			FROM seed_basis_links AS basis
			WHERE basis.tenant_ref = $1
			  AND basis.seed_version_ref = $2
			ORDER BY basis.role, basis.episode_ref
		`, target.Owner.TenantRef, target.VersionRef)
		if err != nil {
			return nil, nil, storageError("load feedback Episode Basis state", err)
		}
		for rows.Next() {
			var role, ref string
			if err := rows.Scan(&role, &ref); err != nil {
				rows.Close()
				return nil, nil, storageError("scan feedback Episode Basis state", err)
			}
			target.BasisState[feedbackBasisStateKey("episode", role, ref)] = struct{}{}
			if role == "formation" || role == "revision" {
				target.Anchors[ref] = struct{}{}
			}
			// A feedback Episode contributes to a SeedVersion once. Keeping an
			// already-consumed Episode eligible makes an overlapping later window
			// re-submit old evidence and can atomically reject genuinely new feedback.
			if role == "reenactment" || role == "inhibition" {
				delete(target.Episodes, ref)
				delete(target.Receipts, ref)
			}
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, nil, storageError("iterate feedback Episode Basis state", err)
		}
		rows.Close()

		rows, err = querier.Query(ctx, `
			SELECT basis.role, basis.outcome_event_ref
			FROM seed_outcome_basis_links AS basis
			WHERE basis.tenant_ref = $1
			  AND basis.seed_version_ref = $2
			ORDER BY basis.role, basis.outcome_event_ref
		`, target.Owner.TenantRef, target.VersionRef)
		if err != nil {
			return nil, nil, storageError("load feedback Outcome Basis state", err)
		}
		for rows.Next() {
			var role, ref string
			if err := rows.Scan(&role, &ref); err != nil {
				rows.Close()
				return nil, nil, storageError("scan feedback Outcome Basis state", err)
			}
			target.BasisState[feedbackBasisStateKey("outcome", role, ref)] = struct{}{}
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, nil, storageError("iterate feedback Outcome Basis state", err)
		}
		rows.Close()

		anchorRefs := sortedMapKeys(target.Anchors)
		if len(anchorRefs) > 0 {
			anchors, anchorOwner, err := loadConsolidationEpisodes(ctx, querier, anchorRefs)
			if err != nil || anchorOwner != target.Owner {
				return nil, nil, consolidation.ErrInvalidWindow
			}
			for _, anchor := range anchors {
				target.AnchorEvidence[anchor.Ref] = anchor
			}
		}
	}

	outcomes := make(map[string]feedbackOutcome, len(outcomeRefs))
	episodeSet := make(map[string]struct{}, len(episodes))
	for _, episode := range episodes {
		episodeSet[episode.Ref] = struct{}{}
	}
	for _, ref := range outcomeRefs {
		var outcome feedbackOutcome
		var owner ownerScope
		err := querier.QueryRow(ctx, `
			SELECT
				outcome.outcome_event_ref, outcome.episode_ref,
				outcome.scope_kind, outcome.tenant_ref, outcome.agent_ref, outcome.relationship_ref,
				source.source_text, source.actor_kind, source.actor_ref,
				outcome.delivery_receipt_refs
			FROM outcome_events AS outcome
			JOIN source_events AS source
			  ON source.tenant_ref = outcome.tenant_ref
			 AND source.scope_kind = outcome.scope_kind
			 AND source.agent_ref = outcome.agent_ref
			 AND source.relationship_ref = outcome.relationship_ref
			 AND source.session_ref = outcome.session_ref
			 AND source.source_ref = outcome.source_event_ref
			WHERE outcome.outcome_event_ref = $1
		`, ref).Scan(
			&outcome.Ref, &outcome.EpisodeRef,
			&owner.Kind, &owner.TenantRef, &owner.AgentRef, &owner.RelationshipRef,
			&outcome.Text, &outcome.ActorKind, &outcome.ActorRef, &outcome.DeliveryRefs,
		)
		if err != nil {
			return nil, nil, consolidation.ErrInvalidWindow
		}
		if owner != episodes[0].Owner {
			return nil, nil, consolidation.ErrInvalidWindow
		}
		if _, exists := episodeSet[outcome.EpisodeRef]; !exists {
			return nil, nil, consolidation.ErrInvalidWindow
		}
		outcomes[ref] = outcome
		for _, target := range targets {
			receipts := target.Receipts[outcome.EpisodeRef]
			if outcome.ActorKind != ledger.ActorKindAgent && intersectsRefs(outcome.DeliveryRefs, receipts) {
				target.Outcomes[ref] = struct{}{}
				target.OutcomeEpisodes[ref] = outcome.EpisodeRef
			}
		}
	}
	return targets, outcomes, nil
}

func consolidationWorkerRequest(
	jobRef string,
	episodes []episodeEvidence,
	relatedEpisodes []episodeEvidence,
	outcomes map[string]feedbackOutcome,
	targets map[string]*feedbackTarget,
	activeDispositions []activeDisposition,
	activeRecollections []activeRecollection,
	formationPairs []dispositionFormationPair,
) consolidation.WorkerRequest {
	evidenceCount := len(episodes) + len(relatedEpisodes)
	directEpisodes := make(map[string]struct{})
	for _, episode := range episodes {
		if episodeHasSituationActor(episode, true) {
			directEpisodes[episode.Ref] = struct{}{}
		}
	}
	canForm := hasFormationEvidence(episodes, relatedEpisodes)
	targetRefs := sortedMapKeys(targets)
	allowedTargetRefs := make([]string, 0, len(targetRefs)+len(activeRecollections)+2)
	if len(episodes) > 0 {
		allowedTargetRefs = append(allowedTargetRefs, consolidation.TargetNewRecollection)
	}
	if canForm || len(directEpisodes) > 0 {
		allowedTargetRefs = append(allowedTargetRefs, consolidation.TargetNewDisposition)
	}
	for _, recollection := range activeRecollections {
		allowedTargetRefs = append(allowedTargetRefs, recollection.VersionRef)
	}
	allowedTargetRefs = append(allowedTargetRefs, targetRefs...)

	basisSet := make(map[string]struct{})
	allowedBasisRefs := make([]string, 0, evidenceCount)
	appendBasis := func(ref string) {
		if _, exists := basisSet[ref]; exists {
			return
		}
		basisSet[ref] = struct{}{}
		allowedBasisRefs = append(allowedBasisRefs, ref)
	}
	for _, episode := range episodes {
		appendBasis(episode.Ref)
	}
	for _, episode := range relatedEpisodes {
		appendBasis(episode.Ref)
	}
	anchorEvidence := make(map[string]episodeEvidence)
	formationOutcomes := eligibleFormationOutcomes(episodes, relatedEpisodes, outcomes)
	for _, pair := range formationPairs {
		formationOutcomes[pair.Outcome.Ref] = struct{}{}
	}
	eligibleOutcomes := make(map[string]struct{})
	for _, ref := range sortedMapKeys(formationOutcomes) {
		eligibleOutcomes[ref] = struct{}{}
		appendBasis(ref)
	}
	for _, target := range targets {
		for ref := range target.Outcomes {
			eligibleOutcomes[ref] = struct{}{}
		}
	}
	var text strings.Builder
	text.WriteString(consolidation.RenderConstitution(currentConstitution(episodes)))
	hints := make([]activeDisposition, 0, len(activeDispositions))
	for _, disposition := range activeDispositions {
		if targets[disposition.VersionRef] == nil {
			hints = append(hints, disposition)
		}
	}
	text.WriteString(renderFeedbackEpisodes(episodes, hints))
	if len(relatedEpisodes) > 0 {
		text.WriteString("\nRELATED_EPISODES\n")
		text.WriteString(renderRelatedEpisodes(relatedEpisodes))
	}
	if len(episodes) > 0 {
		text.WriteString("\nELIGIBLE_NEW_RECOLLECTION NEW_RECOLLECTION\nAPPLICATIONS SELF OTHER RELATION SITUATION\n")
	}
	if canForm {
		fmt.Fprintf(
			&text, "\nELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\nAPPLICATION %s\n",
			dispositionApplication(episodes[0].Owner),
		)
	}
	if len(directEpisodes) > 0 {
		fmt.Fprintf(&text, "\nELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION %s\n", dispositionApplication(episodes[0].Owner))
		for _, ref := range sortedMapKeys(directEpisodes) {
			fmt.Fprintf(&text, "DIRECT_EPISODE %s\n", ref)
		}
	}
	for _, recollection := range activeRecollections {
		fmt.Fprintf(
			&text, "\nELIGIBLE_RECOLLECTION %s\nAPPLICATION %s\nTEXT %s\n",
			recollection.VersionRef, strings.ToUpper(recollection.Application), recollection.Text,
		)
	}
	for _, ref := range sortedMapKeys(eligibleOutcomes) {
		outcome := outcomes[ref]
		fmt.Fprintf(
			&text, "\nOUTCOME %s\nOUTCOME_EPISODE %s\nACTOR %s %s\n%s\n",
			outcome.Ref, outcome.EpisodeRef, outcome.ActorKind, outcome.ActorRef, outcome.Text,
		)
	}
	for _, ref := range targetRefs {
		target := targets[ref]
		if len(target.DirectEpisodes) > 0 {
			fmt.Fprintf(&text, "\nELIGIBLE_ADAPTATION %s\nAPPLICATION %s\nTEXT %s\n", ref, dispositionApplication(target.Owner), target.Tendency)
			for _, episodeRef := range sortedMapKeys(target.DirectEpisodes) {
				fmt.Fprintf(&text, "DIRECT_EPISODE %s\n", episodeRef)
			}
		}
		if len(target.Episodes) > 0 {
			fmt.Fprintf(&text, "\nELIGIBLE_DISPOSITION %s\nAPPLICATION %s\nTEXT %s\n", ref, dispositionApplication(target.Owner), target.Tendency)
		}
		for _, episodeRef := range sortedMapKeys(target.Episodes) {
			fmt.Fprintf(&text, "FEEDBACK_EPISODE %s\n", episodeRef)
			appendBasis(episodeRef)
		}
		for _, outcomeRef := range sortedMapKeys(target.Outcomes) {
			fmt.Fprintf(&text, "FEEDBACK_OUTCOME %s\n", outcomeRef)
			fmt.Fprintf(&text, "OUTCOME_EPISODE %s\n", target.OutcomeEpisodes[outcomeRef])
			appendBasis(outcomeRef)
		}
		// Direct-only candidates need current text and current/related evidence,
		// not every historical anchor in the catalogue. Keep canonical anchors
		// in the snapshot for validation/staleness, outside the model payload.
		if len(target.Episodes) == 0 {
			continue
		}
		for _, anchorRef := range sortedMapKeys(target.Anchors) {
			fmt.Fprintf(&text, "REVISION_ANCHOR %s\n", anchorRef)
			appendBasis(anchorRef)
			if anchor, exists := target.AnchorEvidence[anchorRef]; exists {
				anchorEvidence[anchorRef] = anchor
			}
		}
	}
	anchors := make([]episodeEvidence, 0, len(anchorEvidence))
	if len(anchorEvidence) > 0 {
		for _, ref := range sortedMapKeys(anchorEvidence) {
			anchors = append(anchors, anchorEvidence[ref])
		}
		text.WriteString("\nREVISION_ANCHOR_EVIDENCE\n")
		text.WriteString(renderFeedbackEpisodes(anchors, nil))
	}
	evidence := consolidation.AssembleWindowEvidence(
		workerEvidenceEpisodes(episodes),
		workerEvidenceEpisodes(relatedEpisodes),
		workerEvidenceEpisodes(anchors),
		workerEvidenceOutcomes(outcomes, eligibleOutcomes),
	)
	markDispositionFormationRoles(evidence, formationPairs)
	evidence.Recollections = workerEvidenceRecollections(activeRecollections)
	return consolidation.WorkerRequest{
		JobRef: jobRef, WindowText: text.String(), AllowedTargetRefs: allowedTargetRefs,
		AllowedBasisRefs: allowedBasisRefs,
		Evidence:         evidence,
	}
}

func eligibleFormationOutcomes(
	episodes []episodeEvidence,
	relatedEpisodes []episodeEvidence,
	outcomes map[string]feedbackOutcome,
) map[string]struct{} {
	eligible := make(map[string]struct{})
	if !hasFormationEvidence(episodes, relatedEpisodes) {
		return eligible
	}
	episodeByRef := make(map[string]episodeEvidence, len(episodes)+len(relatedEpisodes))
	for _, values := range [][]episodeEvidence{episodes, relatedEpisodes} {
		for _, episode := range values {
			episodeByRef[episode.Ref] = episode
		}
	}
	for ref, outcome := range outcomes {
		episode, exists := episodeByRef[outcome.EpisodeRef]
		if exists && outcome.ActorKind != "" && outcome.ActorKind != ledger.ActorKindAgent &&
			episodeHasFormationRoles(episode) && episodeHasSituationActor(episode, false) {
			eligible[ref] = struct{}{}
		}
	}
	return eligible
}

func renderFeedbackEpisodes(episodes []episodeEvidence, activeDispositions []activeDisposition) string {
	filtered := make([]episodeEvidence, 0, len(episodes))
	for _, episode := range episodes {
		copy := episode
		copy.Sources = nil
		for _, source := range episode.Sources {
			if source.Role == ledger.RoleSituation || source.Role == ledger.RoleAgentAct {
				copy.Sources = append(copy.Sources, source)
			}
		}
		filtered = append(filtered, copy)
	}
	return renderConsolidationWindow(filtered, activeDispositions, true)
}

func renderRelatedEpisodes(episodes []episodeEvidence) string {
	var text strings.Builder
	for index, episode := range episodes {
		if index > 0 {
			text.WriteByte('\n')
		}
		fmt.Fprintf(&text, "RELATED_EPISODE %s\nSESSION %s\n", episode.Ref, episode.SessionRef)
		for _, source := range episode.Sources {
			if source.Role != ledger.RoleSituation && source.Role != ledger.RoleAgentAct {
				continue
			}
			fmt.Fprintf(&text, "%s\nACTOR %s %s\nSOURCE %s\n%s\n", strings.ToUpper(string(source.Role)), source.ActorKind, source.ActorRef, source.Ref, source.Text)
		}
	}
	return text.String()
}

// Only current Situation admissions choose the baseline; late feedback, caller
// window ordering and related history cannot resurrect an earlier setting.
func currentConstitution(episodes []episodeEvidence) ledger.Constitution {
	var latest sourceEvidence
	for _, episode := range episodes {
		for _, source := range episode.Sources {
			if source.Role == ledger.RoleSituation && source.AdmissionOrder > latest.AdmissionOrder {
				latest = source
			}
		}
	}
	return latest.Constitution
}

func hashConsolidationWorkerRequest(request consolidation.WorkerRequest) [sha256.Size]byte {
	fields := []string{"consolidation-worker-request.v1", request.JobRef, request.WindowText, "targets"}
	fields = append(fields, request.AllowedTargetRefs...)
	fields = append(fields, "basis")
	fields = append(fields, request.AllowedBasisRefs...)
	fields = consolidation.AppendWorkerEvidenceHashFields(fields, request.Evidence)
	return hashFields(fields...)
}

func applyReenactment(ctx context.Context, tx pgx.Tx, owner ownerScope, target *feedbackTarget, refs []string) (bool, error) {
	for _, ref := range refs {
		if _, allowed := target.Episodes[ref]; !allowed {
			return false, nil
		}
		var alreadyBasis bool
		if err := tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1 FROM seed_basis_links
				WHERE tenant_ref = $1 AND seed_version_ref = $2 AND episode_ref = $3
			)
		`, owner.TenantRef, target.VersionRef, ref).Scan(&alreadyBasis); err != nil {
			return false, storageError("check existing reenactment Basis", err)
		}
		if alreadyBasis {
			return false, nil
		}
	}
	applied := false
	for _, episodeRef := range sortedRefs(refs) {
		var alreadyContributed bool
		if err := tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1
				FROM seed_basis_links AS basis
				JOIN episodes AS prior
				  ON prior.tenant_ref = basis.tenant_ref
				 AND prior.episode_ref = basis.episode_ref
				JOIN episodes AS candidate
				  ON candidate.tenant_ref = basis.tenant_ref
				 AND candidate.episode_ref = $3
				WHERE basis.tenant_ref = $1
				  AND basis.seed_version_ref = $2
				  AND basis.role = 'reenactment'
				  AND prior.scope_kind = candidate.scope_kind
				  AND prior.agent_ref = candidate.agent_ref
				  AND prior.relationship_ref = candidate.relationship_ref
				  AND prior.session_ref = candidate.session_ref
				  AND prior.run_ref = candidate.run_ref
			)
		`, owner.TenantRef, target.VersionRef, episodeRef).Scan(&alreadyContributed); err != nil {
			return false, storageError("check reenactment run contribution", err)
		}
		if alreadyContributed {
			continue
		}
		tag, err := tx.Exec(ctx, `
			INSERT INTO seed_basis_links (tenant_ref, seed_version_ref, episode_ref, role)
			VALUES ($1, $2, $3, 'reenactment')
			ON CONFLICT DO NOTHING
		`, owner.TenantRef, target.VersionRef, episodeRef)
		if err != nil {
			return false, storageError("append reenactment Basis", err)
		}
		applied = applied || tag.RowsAffected() == 1
	}
	return applied, nil
}

func applyInhibition(ctx context.Context, tx pgx.Tx, owner ownerScope, target *feedbackTarget, refs []string) (bool, error) {
	episodes, outcomes, valid := classifyFeedbackBasis(target, refs, false)
	if !valid || len(episodes) == 0 || len(outcomes) == 0 ||
		!exactFeedbackEpisodeOutcomeMatch(target, episodes, outcomes) {
		return false, nil
	}
	applied, err := insertEpisodeBasis(ctx, tx, owner.TenantRef, target.VersionRef, episodes, "inhibition")
	if err != nil {
		return false, err
	}
	outcomeApplied, err := insertOutcomeBasis(ctx, tx, owner.TenantRef, target.VersionRef, outcomes, "inhibition")
	return applied || outcomeApplied, err
}

func applyRevision(ctx context.Context, tx pgx.Tx, owner ownerScope, jobRef string, target *feedbackTarget, change consolidation.Change) (string, bool, error) {
	if change.Text == "" || change.Text == target.Tendency {
		return "", false, nil
	}
	var episodes, outcomes []string
	if change.Operation == consolidation.ChangeAdapt {
		if len(change.BasisRefs) != 1 || !basisIncludesCurrentEpisode(change.BasisRefs, target.DirectEpisodes) {
			return "", false, nil
		}
		episodes = sortedRefs(change.BasisRefs)
	} else {
		var valid bool
		episodes, outcomes, valid = classifyFeedbackBasis(target, change.BasisRefs, true)
		if change.Operation != consolidation.ChangeText ||
			!valid || len(episodes) == 0 || len(outcomes) == 0 ||
			!exactFeedbackEpisodeOutcomeMatch(target, episodes, outcomes) {
			return "", false, nil
		}
	}
	var duplicate bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM disposition_seeds AS seed
			JOIN seed_versions AS version
			  ON version.tenant_ref = seed.tenant_ref
			 AND version.seed_ref = seed.seed_ref
			WHERE seed.tenant_ref = $1
			  AND seed.scope_kind = $2
			  AND seed.agent_ref = $3
			  AND seed.relationship_ref = $4
			  AND version.status = 'active'
			  AND version.seed_version_ref <> $5
			  AND version.tendency_text = $6
		)
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, target.VersionRef, change.Text).Scan(&duplicate); err != nil {
		return "", false, storageError("check exact active Seed revision duplicate", err)
	}
	if duplicate {
		return "", false, nil
	}
	newVersionNumber := target.VersionNumber + 1
	newVersionRef := fmt.Sprintf("%s@%d", target.SeedRef, newVersionNumber)
	tag, err := tx.Exec(ctx, `
		UPDATE seed_versions
		SET status = 'superseded'
		WHERE tenant_ref = $1 AND seed_version_ref = $2 AND status = 'active'
	`, owner.TenantRef, target.VersionRef)
	if err != nil {
		return "", false, storageError("supersede revised SeedVersion", err)
	}
	if tag.RowsAffected() != 1 {
		return "", false, consolidation.ErrWindowStale
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO seed_versions (
			tenant_ref, seed_ref, seed_version_ref, version_number,
			tendency_text, status, origin_job_ref
		) VALUES ($1, $2, $3, $4, $5, 'active', $6)
	`, owner.TenantRef, target.SeedRef, newVersionRef, newVersionNumber, change.Text, jobRef); err != nil {
		return "", false, storageError("insert revised SeedVersion", err)
	}
	if _, err := insertEpisodeBasis(ctx, tx, owner.TenantRef, newVersionRef, episodes, "revision"); err != nil {
		return "", false, err
	}
	if _, err := insertOutcomeBasis(ctx, tx, owner.TenantRef, newVersionRef, outcomes, "revision"); err != nil {
		return "", false, err
	}
	return newVersionRef, true, nil
}

func classifyFeedbackBasis(target *feedbackTarget, refs []string, allowAnchors bool) ([]string, []string, bool) {
	episodes := make([]string, 0, len(refs))
	outcomes := make([]string, 0, len(refs))
	for _, ref := range refs {
		switch {
		case hasRef(target.Episodes, ref):
			episodes = append(episodes, ref)
		case allowAnchors && hasRef(target.Anchors, ref):
			episodes = append(episodes, ref)
		case hasRef(target.Outcomes, ref):
			outcomes = append(outcomes, ref)
		default:
			return nil, nil, false
		}
	}
	sort.Strings(episodes)
	sort.Strings(outcomes)
	return episodes, outcomes, true
}

func exactFeedbackEpisodeOutcomeMatch(target *feedbackTarget, episodeRefs, outcomeRefs []string) bool {
	matchedCurrentEpisodes := make(map[string]bool)
	for _, episodeRef := range episodeRefs {
		if hasRef(target.Episodes, episodeRef) {
			matchedCurrentEpisodes[episodeRef] = false
		}
	}
	if len(matchedCurrentEpisodes) == 0 {
		return false
	}
	for _, outcomeRef := range outcomeRefs {
		episodeRef, exists := target.OutcomeEpisodes[outcomeRef]
		if !exists {
			return false
		}
		if _, provided := matchedCurrentEpisodes[episodeRef]; !provided {
			return false
		}
		matchedCurrentEpisodes[episodeRef] = true
	}
	for _, matched := range matchedCurrentEpisodes {
		if !matched {
			return false
		}
	}
	return true
}

func insertEpisodeBasis(ctx context.Context, tx pgx.Tx, tenantRef, versionRef string, refs []string, role string) (bool, error) {
	applied := false
	for _, ref := range refs {
		tag, err := tx.Exec(ctx, `
			INSERT INTO seed_basis_links (tenant_ref, seed_version_ref, episode_ref, role)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT DO NOTHING
		`, tenantRef, versionRef, ref, role)
		if err != nil {
			return false, storageError("append Episode Seed Basis", err)
		}
		applied = applied || tag.RowsAffected() == 1
	}
	return applied, nil
}

func insertOutcomeBasis(ctx context.Context, tx pgx.Tx, tenantRef, versionRef string, refs []string, role string) (bool, error) {
	applied := false
	for _, ref := range refs {
		tag, err := tx.Exec(ctx, `
			INSERT INTO seed_outcome_basis_links (tenant_ref, seed_version_ref, outcome_event_ref, role)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT DO NOTHING
		`, tenantRef, versionRef, ref, role)
		if err != nil {
			return false, storageError("append Outcome Seed Basis", err)
		}
		applied = applied || tag.RowsAffected() == 1
	}
	return applied, nil
}

func hashFeedbackState(targets map[string]*feedbackTarget) [sha256.Size]byte {
	fields := []string{"feedback-state.v1"}
	for _, ref := range sortedMapKeys(targets) {
		target := targets[ref]
		fields = append(fields,
			string(target.Owner.Kind), target.Owner.TenantRef, target.Owner.AgentRef, target.Owner.RelationshipRef,
			target.SeedRef, target.VersionRef, fmt.Sprint(target.VersionNumber), target.Tendency,
		)
		fields = append(fields, sortedMapKeys(target.Episodes)...)
		fields = append(fields, "direct-episodes")
		fields = append(fields, sortedMapKeys(target.DirectEpisodes)...)
		fields = append(fields, "outcomes")
		for _, outcomeRef := range sortedMapKeys(target.Outcomes) {
			fields = append(fields, outcomeRef, target.OutcomeEpisodes[outcomeRef])
		}
		fields = append(fields, "anchors")
		fields = append(fields, sortedMapKeys(target.Anchors)...)
		fields = append(fields, "basis-state")
		fields = append(fields, sortedMapKeys(target.BasisState)...)
		for _, episodeRef := range sortedMapKeys(target.Receipts) {
			fields = append(fields, "receipts", episodeRef)
			fields = append(fields, sortedMapKeys(target.Receipts[episodeRef])...)
		}
	}
	return hashFields(fields...)
}

func feedbackBasisStateKey(kind, role, ref string) string {
	return kind + "\x00" + role + "\x00" + ref
}

func uniqueStableRefsAllowEmpty(refs []string) bool {
	return len(refs) == 0 || uniqueStableRefs(refs)
}

func intersectsRefs(refs []string, allowed map[string]struct{}) bool {
	for _, ref := range refs {
		if _, exists := allowed[ref]; exists {
			return true
		}
	}
	return false
}

func containsFeedbackEpisode(target *feedbackTarget, refs []string) bool {
	for _, ref := range refs {
		if hasRef(target.Episodes, ref) {
			return true
		}
	}
	return false
}

func hasRef(values map[string]struct{}, ref string) bool {
	_, exists := values[ref]
	return exists
}

func sortedMapKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
