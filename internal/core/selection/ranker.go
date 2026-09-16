package selection

import (
	"strings"
	"unicode"
)

const defaultNGramSize = 2

type nGramDiceRanker struct {
	size int
}

type multilingualRanker struct {
	fallback nGramDiceRanker
}

// DefaultRanker uses topic-term overlap for Latin word-boundary text and keeps
// character n-grams as the boundary-free fallback for scripts such as Chinese.
func DefaultRanker() Ranker {
	return multilingualRanker{fallback: nGramDiceRanker{size: defaultNGramSize}}
}

// DefaultPolicy returns a value so callers cannot mutate shared policy state.
func DefaultPolicy() Policy {
	return Policy{
		Ref:                             "indexed-support-episode-latin-term-cjk-bigram-v6",
		MinimumScore:                    0.2,
		MaxRecollections:                8,
		MaxDispositions:                 8,
		AvailabilityCap:                 4,
		CanonicalCandidatesPerOwnerKind: 40,
	}
}

func (ranker multilingualRanker) Score(query, candidate string) float64 {
	left, leftLatin := latinTerms(query)
	right, rightLatin := latinTerms(candidate)
	if leftLatin && rightLatin {
		if len(left) == 0 || len(right) == 0 {
			return 0
		}
		common := 0
		for term := range left {
			if _, exists := right[term]; exists {
				common++
			}
		}
		return 2 * float64(common) / float64(len(left)+len(right))
	}
	return ranker.fallback.Score(query, candidate)
}

func latinTerms(text string) (map[string]struct{}, bool) {
	letters, latinLetters := 0, 0
	for _, value := range text {
		if !unicode.IsLetter(value) {
			continue
		}
		letters++
		if unicode.In(value, unicode.Latin) {
			latinLetters++
		}
	}
	if letters == 0 || latinLetters*2 < letters {
		return nil, false
	}

	terms := make(map[string]struct{})
	for _, field := range strings.FieldsFunc(strings.ToLower(text), func(value rune) bool {
		return !unicode.IsLetter(value) && !unicode.IsDigit(value)
	}) {
		if len([]rune(field)) < 2 {
			continue
		}
		if _, ignored := latinStopTerms[field]; ignored {
			continue
		}
		terms[field] = struct{}{}
	}
	return terms, true
}

// These terms carry sentence structure or generic memory/recommendation
// framing, not the topic that should make two memories relevant.
var latinStopTerms = map[string]struct{}{
	"a": {}, "about": {}, "after": {}, "all": {}, "also": {}, "am": {}, "an": {}, "and": {},
	"any": {}, "are": {}, "as": {}, "at": {}, "be": {}, "been": {}, "being": {}, "but": {},
	"by": {}, "can": {}, "could": {}, "did": {}, "do": {}, "does": {}, "doing": {}, "for": {},
	"from": {}, "had": {}, "has": {}, "have": {}, "having": {}, "he": {}, "her": {}, "here": {},
	"him": {}, "his": {}, "how": {}, "i": {}, "if": {}, "in": {}, "into": {}, "is": {}, "it": {},
	"its": {}, "me": {}, "more": {}, "most": {}, "my": {}, "no": {}, "not": {}, "of": {}, "on": {},
	"or": {}, "our": {}, "out": {}, "over": {}, "please": {}, "she": {}, "should": {}, "so": {},
	"some": {}, "than": {}, "that": {}, "the": {}, "their": {}, "them": {}, "then": {}, "there": {},
	"these": {}, "they": {}, "this": {}, "those": {}, "to": {}, "too": {}, "under": {}, "up": {},
	"very": {}, "was": {}, "we": {}, "were": {}, "what": {}, "when": {}, "where": {}, "which": {},
	"while": {}, "who": {}, "why": {}, "will": {}, "with": {}, "would": {}, "you": {}, "your": {},
	"enjoyment": {}, "future": {}, "preference": {}, "preferences": {}, "previously": {},
	"recommend": {}, "recommendation": {}, "recommendations": {}, "stated": {}, "use": {}, "user": {},
}

func (ranker nGramDiceRanker) Score(query, candidate string) float64 {
	left := nGrams(normalizeText(query), ranker.size)
	right := nGrams(normalizeText(candidate), ranker.size)
	if len(left) == 0 || len(right) == 0 {
		return 0
	}

	common := 0
	for gram, leftCount := range left {
		rightCount := right[gram]
		if rightCount < leftCount {
			common += rightCount
		} else {
			common += leftCount
		}
	}
	return 2 * float64(common) / float64(gramCount(left)+gramCount(right))
}

func normalizeText(text string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return unicode.ToLower(r)
	}, strings.TrimSpace(text))
}

func nGrams(text string, size int) map[string]int {
	runes := []rune(text)
	if len(runes) == 0 {
		return nil
	}
	if size <= 0 {
		size = defaultNGramSize
	}
	if len(runes) < size {
		return map[string]int{text: 1}
	}
	grams := make(map[string]int, len(runes)-size+1)
	for index := 0; index <= len(runes)-size; index++ {
		grams[string(runes[index:index+size])]++
	}
	return grams
}

func gramCount(grams map[string]int) int {
	total := 0
	for _, count := range grams {
		total += count
	}
	return total
}
