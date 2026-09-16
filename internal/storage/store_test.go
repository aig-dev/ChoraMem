package storage

import (
	"reflect"
	"sort"
	"testing"
)

func TestCoreStoreMethodSetIsMinimalAndFrozen(t *testing.T) {
	t.Parallel()

	typeOfStore := reflect.TypeOf((*CoreStore)(nil)).Elem()
	got := make([]string, 0, typeOfStore.NumMethod())
	for index := 0; index < typeOfStore.NumMethod(); index++ {
		got = append(got, typeOfStore.Method(index).Name)
	}
	sort.Strings(got)

	want := []string{
		"AcknowledgeMemoryIndexOperation",
		"Close",
		"CompleteConsolidationJob",
		"ConsolidateWindow",
		"EnumerateMemoryIndexDocuments",
		"LeaseConsolidationJob",
		"LeaseMemoryIndexOperation",
		"MemoryIndexDocument",
		"Migrate",
		"Observe",
		"Ping",
		"RecordMemoryDelivery",
		"ReportOutcome",
		"RetryConsolidationJob",
		"RetryMemoryIndexOperation",
		"SelectMemory",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("CoreStore methods = %q; want frozen minimal set %q", got, want)
	}
}
