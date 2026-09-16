// Package consolidation parses the transient text contract produced by the
// semantic consolidation worker.
package consolidation

import (
	"fmt"
	"strings"
	"unicode"
)

const (
	TargetNewRecollection = "NEW_RECOLLECTION"
	TargetNewDisposition  = "NEW_DISPOSITION"

	ApplicationSelf      = "SELF"
	ApplicationOther     = "OTHER"
	ApplicationRelation  = "RELATION"
	ApplicationSituation = "SITUATION"

	ChangeText    = "TEXT"
	ChangeAdapt   = "ADAPT"
	ChangeKeep    = "KEEP"
	ChangeReenact = "REENACT"
	ChangeInhibit = "INHIBIT"
)

// Change is one transient semantic update. Persistence, target eligibility,
// and identifier allocation remain the caller's responsibility.
type Change struct {
	Target      string
	Application string
	Operation   string
	Text        string
	BasisRefs   []string
}

// ParseTaggedText parses independent TARGET / APPLICATION / CHANGE / BASIS
// blocks. It validates only the text grammar; Core validates target kind,
// operation, scope, and basis eligibility against the frozen window.
func ParseTaggedText(text string) ([]Change, error) {
	if len(text) > MaxTaggedTextBytes {
		return nil, fmt.Errorf("tagged text exceeds %d bytes", MaxTaggedTextBytes)
	}
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, nil
	}

	rawLines := strings.Split(text, "\n")
	lines := make([]string, 0, len(rawLines))
	for _, line := range rawLines {
		line = strings.TrimSpace(line)
		if line != "" {
			lines = append(lines, line)
		}
	}

	changes := make([]Change, 0, 1)
	existingTargets := make(map[string]struct{})
	for offset := 0; offset < len(lines); {
		if len(lines)-offset < 8 {
			return nil, fmt.Errorf("incomplete change block at line %d", offset+1)
		}
		if lines[offset] != "TARGET" || lines[offset+2] != "APPLICATION" ||
			lines[offset+4] != "CHANGE" {
			return nil, fmt.Errorf("invalid change block markers at line %d", offset+1)
		}

		target := lines[offset+1]
		isNewTarget := target == TargetNewRecollection || target == TargetNewDisposition
		if !isNewTarget && !existingStableRef(target) {
			return nil, fmt.Errorf("invalid target ref at line %d", offset+2)
		}
		if !isNewTarget {
			if _, seen := existingTargets[target]; seen {
				return nil, fmt.Errorf("duplicate existing target ref at line %d", offset+2)
			}
			existingTargets[target] = struct{}{}
		}

		application := lines[offset+3]
		if !validApplication(application) {
			return nil, fmt.Errorf("invalid application at line %d", offset+4)
		}

		operationLine := lines[offset+5]
		basisMarker := offset + 6
		operation, changeText, err := parseOperation(operationLine, offset+6)
		if operationLine == ChangeText || operationLine == ChangeAdapt {
			operation = operationLine
			changeText = lines[offset+6]
			if len(changeText) > MaxChangeTextBytes {
				return nil, fmt.Errorf("text exceeds %d bytes at line %d", MaxChangeTextBytes, offset+7)
			}
			basisMarker++
			err = nil
		}
		if err != nil {
			return nil, err
		}
		if basisMarker >= len(lines) || lines[basisMarker] != "BASIS" {
			return nil, fmt.Errorf("invalid change block markers at line %d", offset+1)
		}

		basisStart := basisMarker + 1
		basisEnd := basisStart
		seenBasis := make(map[string]struct{})
		for basisEnd < len(lines) && lines[basisEnd] != "TARGET" {
			basis := lines[basisEnd]
			if !stableBasisRef(basis) {
				return nil, fmt.Errorf("invalid basis ref at line %d", basisEnd+1)
			}
			if _, seen := seenBasis[basis]; seen {
				return nil, fmt.Errorf("duplicate basis ref at line %d", basisEnd+1)
			}
			seenBasis[basis] = struct{}{}
			basisEnd++
		}
		if basisEnd == basisStart {
			return nil, fmt.Errorf("basis is required at line %d", basisStart+1)
		}

		changes = append(changes, Change{
			Target:      target,
			Application: application,
			Operation:   operation,
			Text:        changeText,
			BasisRefs:   append([]string(nil), lines[basisStart:basisEnd]...),
		})
		offset = basisEnd
	}
	return changes, nil
}

func parseOperation(line string, lineNumber int) (string, string, error) {
	for _, operation := range []string{ChangeText, ChangeAdapt} {
		if !strings.HasPrefix(line, operation+" ") {
			continue
		}
		text := strings.TrimSpace(strings.TrimPrefix(line, operation+" "))
		if text == "" {
			return "", "", fmt.Errorf("text is required at line %d", lineNumber)
		}
		if len(text) > MaxChangeTextBytes {
			return "", "", fmt.Errorf("text exceeds %d bytes at line %d", MaxChangeTextBytes, lineNumber)
		}
		return operation, text, nil
	}
	switch {
	case line == ChangeKeep, line == ChangeReenact, line == ChangeInhibit:
		return line, "", nil
	default:
		return "", "", fmt.Errorf("invalid change at line %d", lineNumber)
	}
}

func validApplication(application string) bool {
	switch application {
	case ApplicationSelf, ApplicationOther, ApplicationRelation, ApplicationSituation:
		return true
	default:
		return false
	}
}

func existingStableRef(ref string) bool {
	return stableRef(ref) && ref != "NEW"
}

func stableBasisRef(ref string) bool {
	if !stableRef(ref) {
		return false
	}
	switch ref {
	case "TARGET", "APPLICATION", "CHANGE", "BASIS", "NEW", TargetNewRecollection, TargetNewDisposition:
		return false
	default:
		return true
	}
}

func stableRef(ref string) bool {
	return ref != "" && strings.IndexFunc(ref, func(r rune) bool {
		return unicode.IsSpace(r) || unicode.IsControl(r)
	}) == -1
}
