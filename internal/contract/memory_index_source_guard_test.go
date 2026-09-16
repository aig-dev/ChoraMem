package contract_test

import (
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestActiveMemoryCoreSourceContainsNoRetiredIndexName(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve contract test path")
	}
	root := filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
	retiredNames := []string{
		"seed" + "index",
		"seed" + "_" + "index",
		"seed" + "-" + "index",
	}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if entry.IsDir() {
			if relative == ".git" || strings.HasPrefix(relative, "bin") {
				return filepath.SkipDir
			}
			for _, retired := range retiredNames {
				if strings.Contains(strings.ToLower(relative), retired) {
					t.Errorf("active directory retains retired index name: %s", relative)
				}
			}
			return nil
		}
		if strings.HasSuffix(path, "_test.go") || (!strings.HasSuffix(path, ".go") && !strings.HasSuffix(path, ".proto")) {
			return nil
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, retired := range retiredNames {
			if strings.Contains(strings.ToLower(string(contents)), retired) {
				t.Errorf("active source retains retired index name: %s", relative)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk Memory Core source: %v", err)
	}
}
