package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestSavedTerminalRecordCannotBecomeSuccessfulMissingOutput(t *testing.T) {
	for _, tc := range []struct {
		name, record string
		failed       bool
	}{
		{"cancelled", `{"input":"public input","attempt_id":"frozen-cancelled","error_type":"CancelledError"}`, true},
		{"failed_with_output", `{"output":"","error_type":"RuntimeError"}`, true},
		{"error", `{"output":"","error":"request failed"}`, true},
		{"missing", `{"input":"public input"}`, true},
		{"null", `{"output":null}`, true},
		{"started", `{"status":"started","output":""}`, true},
		{"failed_status", `{"status":"failed","output":""}`, true},
		{"explicit_empty", `{"input":"public input","output":""}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "windows.jsonl")
			if err := os.WriteFile(path, []byte(tc.record+"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			output, err := exec.Command("go", "run", ".", path).CombinedOutput()
			if (err != nil) != tc.failed {
				t.Fatalf("failed=%v want %v: %s", err != nil, tc.failed, output)
			}
		})
	}
}
