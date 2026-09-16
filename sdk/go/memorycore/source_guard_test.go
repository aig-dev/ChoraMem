package memorycore

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestGoSDKContainsNoActiveLegacyMemoryNames(t *testing.T) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate source guard")
	}
	root := filepath.Clean(filepath.Join(filepath.Dir(currentFile), ".."))
	persona := "Per" + "sona"
	forbidden := []string{persona, "Assemble" + persona, "Context" + "Delivery"}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || path == currentFile {
			return nil
		}
		extension := filepath.Ext(path)
		if extension != ".go" && extension != ".md" {
			return nil
		}
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, name := range forbidden {
			if strings.Contains(string(content), name) {
				t.Errorf("%s contains removed SDK name %q", path, name)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk Go SDK: %v", err)
	}
}
