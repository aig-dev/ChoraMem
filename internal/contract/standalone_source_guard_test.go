package contract_test

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStandaloneSourceHasNoChoraiReverseDependency(t *testing.T) {
	root := filepath.Clean("../..")
	forbidden := []string{
		".." + "/chorai-go",
		"compose." + "chorai.yaml",
		"github.com/aig-dev/" + "Chorai_Go",
	}

	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			switch entry.Name() {
			case ".git", ".pytest_cache", ".venv", "__pycache__", "bin", "dist", "node_modules":
				return filepath.SkipDir
			}
			return nil
		}
		if !isTextSource(path) {
			return nil
		}

		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, fragment := range forbidden {
			if strings.Contains(string(content), fragment) {
				relative, relErr := filepath.Rel(root, path)
				if relErr != nil {
					return relErr
				}
				t.Errorf("standalone source %s contains forbidden reverse dependency %q", relative, fragment)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("scan standalone source: %v", err)
	}
}

func isTextSource(path string) bool {
	base := filepath.Base(path)
	if base == "Dockerfile" || base == "Makefile" || base == "LICENSE" ||
		strings.HasPrefix(base, ".env") || strings.HasSuffix(base, "ignore") {
		return true
	}
	switch filepath.Ext(path) {
	case ".go", ".json", ".jsonl", ".lock", ".md", ".mod", ".proto", ".py", ".sh", ".sql", ".sum", ".toml", ".yaml", ".yml":
		return true
	default:
		return false
	}
}
