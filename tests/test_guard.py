from app.services.mismatch_guard import (
    Candidate, Decision, GuardThresholds, ImageProfile, PostProfile,
    ReasonCode, evaluate_match, normalize_name, select_best, subject_matches,
)

T = GuardThresholds(min_similarity=0.70, min_confidence=0.60)
FOX_POST = PostProfile(expected_subject="fox", expected_category="animal")


def img(subject, category="animal", confidence=0.94, status="valid", aliases=()):
    return ImageProfile(subject, category, confidence, status, tuple(aliases))


def codes(result):
    return [r.code for r in result.reasons]


# ----- head_noun -----

def test_normalize_name_handles_case_plurals_and_hyphens():
    assert normalize_name("Red Foxes") == "red fox"
    assert normalize_name("wolves") == "wolf"
    assert normalize_name("bee-eater") == "bee eater"


def test_specific_post_rejects_a_different_species():
    assert not subject_matches("red fox", ["arctic fox"])
    assert not subject_matches("fox", ["gray wolf"])


def test_generic_post_matches_any_kind_of_that_animal():
    assert subject_matches("fox", ["arctic fox"])      # documented behaviour


def test_alias_lets_a_general_post_find_a_specific_image():
    cub = img("black bear cub", aliases=["bear"])
    pup = img("golden retriever puppy", aliases=["dog"])
    assert evaluate_match(PostProfile("bear", "animal"), cub, 0.9, T).reasons == ()
    assert evaluate_match(PostProfile("dog", "animal"), pup, 0.9, T).reasons == ()


# ----- evaluate_match -----

def test_correct_fox_passes():
    result = evaluate_match(FOX_POST, img("red fox"), 0.91, T)
    assert result.decision == Decision.SUGGESTED
    assert result.reasons == ()


def test_wolf_rejected_for_fox_post_even_with_high_similarity():
    result = evaluate_match(FOX_POST, img("gray wolf"), 0.88, T)
    assert result.decision == Decision.REJECTED
    assert codes(result) == [ReasonCode.SUBJECT_MISMATCH]
    assert "expected fox, detected gray wolf" in result.explanation


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
    assert ReasonCode.UNCLEAR_IMAGE in codes(result)


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

def test_phrase_can_appear_inside_a_longer_name():
    assert subject_matches("black bear", ["black bear cub"])
    assert not subject_matches("red fox", ["arctic fox"])

def test_select_best_evaluates_every_candidate():
    ranked = [Candidate(2, img("red fox"), 0.84), Candidate(1, img("gray wolf"), 0.80)]
    selection = select_best(FOX_POST, ranked, T)
    assert selection.chosen.image_id == 2
    assert len(selection.evaluations) == 2
    assert selection.evaluations[1][1].decision == Decision.REJECTED