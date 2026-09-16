package selection

const (
	MaxEpisodeEvidenceBytes   = 16 * 1024
	MaxEpisodeEvidenceEntries = 8
)

func ValidEpisodeEvidenceMaxBytes(value int) bool {
	return value >= 0 && value <= MaxEpisodeEvidenceBytes
}

// BoundEpisodeEvidence preserves semantic order, skips entries that do not fit
// whole, and caps the public lane independently of learned Memory selection.
func BoundEpisodeEvidence(items []EpisodeEvidence, maxBytes int) []EpisodeEvidence {
	if maxBytes <= 0 {
		return nil
	}
	result := make([]EpisodeEvidence, 0, min(len(items), MaxEpisodeEvidenceEntries))
	used := 0
	for _, item := range items {
		if len(result) == MaxEpisodeEvidenceEntries {
			break
		}
		itemBytes := len([]byte(item.Text))
		if itemBytes > maxBytes-used {
			continue
		}
		result = append(result, item)
		used += itemBytes
	}
	return result
}
