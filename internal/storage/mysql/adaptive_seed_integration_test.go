package mysql_test

import (
	"context"
	"database/sql"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	storemysql "github.com/aig-dev/ChoraMem/internal/storage/mysql"
)

func TestDirectAdaptationPersistsOnlyVersionsAndEpisodeBasis(t *testing.T) {
	dsn := isolatedMySQLDSN(t, testMySQLDatabaseURL(t))
	store, err := storemysql.New(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	if err := store.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	first := materializeMySQLConsolidationEpisode(t, store, "adapt-initial", "以后先听完再建议")
	second := materializeMySQLConsolidationEpisode(t, store, "adapt-correction", "以后先承认担忧再建议")
	third := materializeMySQLConsolidationEpisode(t, store, "adapt-same-text", "以后先承认担忧再建议")
	target := consolidation.TargetNewDisposition
	var revisedVersion string
	for index, episode := range []string{first, second, third} {
		text := "先听完再建议"
		if index > 0 {
			text = "先承认担忧再建议"
		}
		worker := &mysqlConsolidationWorker{taggedText: fmt.Sprintf("TARGET\n%s\nAPPLICATION\nRELATION\nCHANGE\nADAPT\n%s\nBASIS\n%s", target, text, episode)}
		window := consolidation.Window{JobRef: fmt.Sprintf("adapt-provenance-%d", index), EpisodeRefs: []string{episode}}
		result, err := store.ConsolidateWindow(context.Background(), window, worker)
		if err != nil {
			t.Fatal(err)
		}
		if index < 2 {
			if len(result.DispositionVersionRefs) != 1 {
				t.Fatalf("ADAPT %d = %#v", index, result)
			}
			target = result.DispositionVersionRefs[0]
			revisedVersion = target
		} else if len(result.DispositionVersionRefs) != 0 {
			t.Fatalf("same text created version: %#v", result)
		}
		retry, err := store.ConsolidateWindow(context.Background(), window, &mysqlConsolidationWorker{taggedText: "must not execute"})
		if err != nil || !reflect.DeepEqual(retry, result) {
			t.Fatalf("retry changed receipt: %#v, %v", retry, err)
		}
	}
	database, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	for table, want := range map[string]int{
		"disposition_seeds": 1, "seed_versions": 2, "seed_basis_links": 2,
		"seed_outcome_basis_links": 0, "memory_context_items": 0,
		"memory_contexts": 0, "memory_delivery_receipts": 0, "outcome_events": 0,
	} {
		var count int
		if err := database.QueryRow("SELECT count(*) FROM " + table).Scan(&count); err != nil || count != want {
			t.Fatalf("%s count = %d, %v; want %d", table, count, err, want)
		}
	}
	if !strings.HasSuffix(revisedVersion, "@2") {
		t.Fatalf("direct revision lost Seed continuity: %s", revisedVersion)
	}
	for role, want := range map[string]int{"formation": 1, "revision": 1, "reenactment": 0, "inhibition": 0} {
		var count int
		if err := database.QueryRow("SELECT count(*) FROM seed_basis_links WHERE role = ?", role).Scan(&count); err != nil || count != want {
			t.Fatalf("%s Basis = %d, %v; want %d", role, count, err, want)
		}
	}
	var superseded int
	if err := database.QueryRow("SELECT count(*) FROM seed_versions WHERE status = 'superseded'").Scan(&superseded); err != nil || superseded != 1 {
		t.Fatalf("superseded versions = %d, %v; want 1", superseded, err)
	}
}
