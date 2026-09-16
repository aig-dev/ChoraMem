package selection

import "testing"

func TestDefaultRankerHandlesChineseAndLatinText(t *testing.T) {
	ranker := DefaultRanker()

	if got := ranker.Score("PLEASE KEEP IT CONCISE", "please keep it concise"); got != 1 {
		t.Fatalf("exact case-folded Score() = %v; want 1", got)
	}
	relevant := ranker.Score("请简洁回答我的问题", "倾向于用简洁方式回答问题")
	unrelated := ranker.Score("请简洁回答我的问题", "倾向于主动安排日历和会议")
	if relevant <= unrelated {
		t.Fatalf("Chinese relevant Score() = %v; unrelated = %v; want relevant score greater", relevant, unrelated)
	}
}

func TestDefaultRankerDoesNotPromoteEnglishFunctionWordOverlap(t *testing.T) {
	ranker := DefaultRanker()
	policy := DefaultPolicy()

	query := "What TV series have imaginative storytelling and complex worlds for a long weekend binge?"
	unrelated := "User visited a science center and took notes for a lecture on statistical modeling of complex behavior."
	if got := ranker.Score(query, unrelated); got >= policy.MinimumScore {
		t.Fatalf("unrelated English Score() = %v; want below selection minimum %v", got, policy.MinimumScore)
	}

	relevant := "User prefers imaginative television series with complex fictional worlds."
	if got := ranker.Score(query, relevant); got < policy.MinimumScore {
		t.Fatalf("relevant English Score() = %v; want at least selection minimum %v", got, policy.MinimumScore)
	}
}

func TestDefaultRankerPrefersCompleteLatinTopicMatchOverSubset(t *testing.T) {
	ranker := DefaultRanker()
	complete := ranker.Score("stronger agent memory", "stronger agent memory")
	subset := ranker.Score("stronger agent memory", "stronger agent")
	if complete <= subset {
		t.Fatalf("complete Score() = %v, subset = %v; want complete match higher", complete, subset)
	}
}

func TestDefaultPolicyIsVersionedAndBounded(t *testing.T) {
	policy := DefaultPolicy()
	if policy.Ref == "" {
		t.Fatal("DefaultPolicy().Ref is empty; want versioned policy identity")
	}
	if policy.MinimumScore <= 0 || policy.MinimumScore > 1 {
		t.Fatalf("DefaultPolicy().MinimumScore = %v; want (0, 1]", policy.MinimumScore)
	}
	if policy.MaxRecollections <= 0 || policy.MaxDispositions <= 0 || policy.AvailabilityCap <= 0 {
		t.Fatalf("DefaultPolicy() = %#v; want positive output and availability bounds", policy)
	}
}
