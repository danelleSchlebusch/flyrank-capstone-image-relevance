from app.services.mismatch_guard import (
    Candidate, Decision, GuardThresholds, ImageProfile, PostProfile,
    ReasonCode, evaluate_match, head_noun, select_best,
)

T = GuardThresholds(min_similarity=0.70, min_confidence=0.60)
FOX_POST = PostProfile(expected_subject="fox", expected_category="animal")


def img(subject, category="animal", confidence=0.94, status="valid"):
    return ImageProfile(subject, category, confidence, status)


def codes(result):
    return [r.code for r in result.reasons]


# ----- head_noun -----

def test_head_noun_ignores_modifiers_and_plurals():
    assert head_noun("red fox") == "fox"
    assert head_noun("Red Foxes") == "fox"
    assert head_noun("gray wolf") == "wolf"
    assert head_noun("wolves") == "wolf"


# ----- evaluate_match -----

def test_correct_fox_passes():
    result = evaluate_match(FOX_POST, img("red fox"), 0.91, T)
    assert result.decision == Decision.SUGGESTED
    assert result.reasons == ()


def test_wolf_rejected_for_fox_post_even_with_high_similarity():
    result = evaluate_match(FOX_POST, img("gray wolf"), 0.88, T)
    assert result.decision == Decision.REJECTED
    assert codes(result) == [ReasonCode.SUBJECT_MISMATCH]
    assert "expected fox, detected wolf" in result.explanation


def test_generic_dog_rejected_for_fox_post():
    result = evaluate_match(FOX_POST, img("dog"), 0.80, T)
    assert result.decision == Decision.REJECTED
    assert ReasonCode.SUBJECT_MISMATCH in codes(result)


def test_different_category_rejected():
    result = evaluate_match(FOX_POST, img("sports car", category="vehicle"), 0.85, T)
    assert codes(result) == [ReasonCode.CATEGORY_MISMATCH]
    assert "expected animal, detected vehicle" in result.explanation


def test_low_similarity_rejected():
    result = evaluate_match(FOX_POST, img("red fox"), 0.40, T)
    assert codes(result) == [ReasonCode.LOW_SIMILARITY]


def test_low_model_confidence_rejected():
    result = evaluate_match(FOX_POST, img("red fox", confidence=0.30), 0.91, T)
    assert codes(result) == [ReasonCode.LOW_CONFIDENCE]


def test_flagged_image_rejected_even_if_confidence_number_is_high():
    result = evaluate_match(FOX_POST, img("red fox", status="low_confidence"), 0.91, T)
    assert ReasonCode.LOW_CONFIDENCE in codes(result)


def test_unknown_post_subject_is_rejected_not_guessed():
    post = PostProfile(expected_subject=None, expected_category=None)
    result = evaluate_match(post, img("red fox"), 0.95, T)
    assert ReasonCode.MISSING_POST_SUBJECT in codes(result)


def test_all_failures_are_reported_together():
    result = evaluate_match(FOX_POST, img("gray wolf", confidence=0.2), 0.30, T)
    assert set(codes(result)) == {
        ReasonCode.SUBJECT_MISMATCH,
        ReasonCode.LOW_CONFIDENCE,
        ReasonCode.LOW_SIMILARITY,
    }


# ----- select_best -----

def test_select_best_skips_wolf_and_picks_fox():
    ranked = [
        Candidate(1, img("gray wolf"), 0.88),   # ranks first, but wrong
        Candidate(2, img("red fox"), 0.84),
    ]
    selection = select_best(FOX_POST, ranked, T)
    assert selection.decision == Decision.SUGGESTED
    assert selection.chosen.image_id == 2
    assert selection.evaluations[0][1].decision == Decision.REJECTED


def test_no_confident_match_when_nothing_passes():
    ranked = [Candidate(1, img("gray wolf"), 0.88), Candidate(3, img("dog"), 0.80)]
    selection = select_best(FOX_POST, ranked, T)
    assert selection.decision == Decision.NO_CONFIDENT_MATCH
    assert selection.chosen is None
    assert "wolf" in selection.explanation


def test_no_candidates_at_all():
    selection = select_best(FOX_POST, [], T)
    assert selection.decision == Decision.NO_CONFIDENT_MATCH