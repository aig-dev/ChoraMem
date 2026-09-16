// adaptivecheck applies the production parser and offered-ref boundary to saved
// model text. It is not a semantic judge or a substitute for database eligibility.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
)

func main() {
	if len(os.Args) != 2 {
		panic("usage: go run ./eval/adaptivecheck <windows.jsonl>")
	}
	file, err := os.Open(os.Args[1])
	if err != nil {
		panic(err)
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	failed := false
	for {
		var row struct {
			Case, Input, Error, Status string
			Output                     *string
			ErrorType                  string `json:"error_type"`
		}
		if err := decoder.Decode(&row); err == io.EOF {
			break
		} else if err != nil {
			panic(err)
		}
		problem := row.Error
		if row.ErrorType != "" {
			problem = row.ErrorType
		}
		if row.Status != "" && row.Status != "completed" && row.Status != "success" {
			problem = "non-success record status: " + row.Status
		}
		if row.Output == nil && problem == "" {
			problem = "missing output in terminal record"
		}
		var changes []consolidation.Change
		if problem == "" {
			changes, err = consolidation.ParseTaggedText(*row.Output)
			if err != nil {
				problem = err.Error()
			}
		}
		allowed := map[string]map[string]bool{"TARGET": {}, "BASIS": {}}
		head := strings.SplitN(row.Input, "\nWINDOW_BEGIN\n", 2)[0]
		for _, match := range regexp.MustCompile(`(?m)^ALLOWED_(BASIS|TARGET)\n([^\n]+)$`).FindAllStringSubmatch(head, -1) {
			allowed[match[1]][match[2]] = true
		}
		for _, change := range changes {
			if !allowed["TARGET"][change.Target] {
				problem = "unoffered target: " + change.Target
			}
			for _, ref := range change.BasisRefs {
				if !allowed["BASIS"][ref] {
					problem = "unoffered basis: " + ref
				}
			}
		}
		failed = failed || problem != ""
		if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"case": row.Case, "changes": changes, "error": problem}); err != nil {
			panic(err)
		}
	}
	if failed {
		fmt.Fprintln(os.Stderr, "production grammar/offered-ref failure; no semantic or DB verdict implied")
		os.Exit(1)
	}
}
