"""
Mismatch guard: decides whether a ranked candidate image is actually
good enough for a post.

This module is deliberately pure: no database, no AI calls, no settings.
Everything it needs is passed in, so it can be tested in isolation.
"""
import re
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    SUGGESTED = "suggested"
    REJECTED = "rejected"
    NO_CONFIDENT_MATCH = "no_confident_match"


class ReasonCode(str, Enum):
    MISSING_POST_SUBJECT = "missing_post_subject"
    CATEGORY_MISMATCH = "category_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    LOW_SIMILARITY = "low_similarity"


# ---------- Inputs ----------

@dataclass(frozen=True)
class PostProfile:
    """What the post is about (extracted by the AI when the post is created)."""
    expected_subject: str | None
    expected_category: str | None


@dataclass(frozen=True)
class ImageProfile:
    """What the vision model detected in the image."""
    subject: str
    category: str
    confidence: float
    validation_status: str = "valid"  # "valid" | "low_confidence"


@dataclass(frozen=True)
class GuardThresholds:
    min_similarity: float
    min_confidence: float


@dataclass(frozen=True)
class Candidate:
    """One ranked image, as produced by the similarity search."""
    image_id: int
    image: ImageProfile
    similarity: float


# ---------- Outputs ----------

@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    message: str


@dataclass(frozen=True)
class GuardResult:
    """Empty reasons means the candidate passed every check."""
    reasons: tuple[Reason, ...] = ()

    @property
    def decision(self) -> Decision:
        return Decision.REJECTED if self.reasons else Decision.SUGGESTED

    @property
    def explanation(self) -> str:
        if not self.reasons:
            return "Subject, category, confidence and similarity all meet the required thresholds."
        return "; ".join(r.message for r in self.reasons)


@dataclass(frozen=True)
class GuardSelection:
    decision: Decision
    chosen: Candidate | None
    evaluations: tuple[tuple[Candidate, GuardResult], ...]
    explanation: str


# ---------- Subject comparison ----------

_IRREGULAR_PLURALS = {
    "wolves": "wolf", "mice": "mouse", "geese": "goose",
    "people": "person", "men": "man", "women": "woman",
}


def _singular(word: str) -> str:
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("xes", "ches", "shes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def head_noun(subject: str) -> str:
    """
    Reduce a subject to its core noun, so 'red fox', 'Red Foxes' and 'fox'
    all compare equal, while 'gray wolf' stays different.
    """
    words = re.findall(r"[a-z]+", subject.lower())
    return _singular(words[-1]) if words else ""


def _clean(text: str) -> str:
    return text.strip().lower()


# ---------- The guard ----------

def evaluate_match(
    post: PostProfile,
    image: ImageProfile,
    similarity: float,
    thresholds: GuardThresholds,
) -> GuardResult:
    reasons: list[Reason] = []

    expected = head_noun(post.expected_subject or "")
    detected = head_noun(image.subject)

    if not expected:
        reasons.append(Reason(
            ReasonCode.MISSING_POST_SUBJECT,
            "The post's subject could not be determined, so the image cannot be verified",
        ))

    category_ok = True
    if post.expected_category and _clean(post.expected_category) != _clean(image.category):
        category_ok = False
        reasons.append(Reason(
            ReasonCode.CATEGORY_MISMATCH,
            f"Category mismatch: expected {_clean(post.expected_category)}, "
            f"detected {_clean(image.category)}",
        ))

    # Only compare subjects when the category already matches; otherwise
    # the category reason already explains the problem.
    if expected and category_ok and expected != detected:
        reasons.append(Reason(
            ReasonCode.SUBJECT_MISMATCH,
            f"{_clean(image.category).capitalize()} category mismatch: "
            f"expected {expected}, detected {detected}",
        ))

    if (
        image.validation_status == "low_confidence"
        or image.confidence < thresholds.min_confidence
    ):
        reasons.append(Reason(
            ReasonCode.LOW_CONFIDENCE,
            f"Low classification confidence: {image.confidence:.2f} "
            f"(minimum {thresholds.min_confidence:.2f})",
        ))

    if similarity < thresholds.min_similarity:
        reasons.append(Reason(
            ReasonCode.LOW_SIMILARITY,
            f"Similarity {similarity:.2f} is below the threshold "
            f"{thresholds.min_similarity:.2f}",
        ))

    return GuardResult(reasons=tuple(reasons))


def select_best(
    post: PostProfile,
    ranked_candidates: list[Candidate],
    thresholds: GuardThresholds,
) -> GuardSelection:
    """
    Walk the ranked list from best to worst and return the first candidate
    that passes the guard. If none passes, answer 'no confident match'.
    """
    evaluations = []
    for candidate in ranked_candidates:
        result = evaluate_match(post, candidate.image, candidate.similarity, thresholds)
        evaluations.append((candidate, result))
        if result.decision == Decision.SUGGESTED:
            return GuardSelection(
                decision=Decision.SUGGESTED,
                chosen=candidate,
                evaluations=tuple(evaluations),
                explanation=result.explanation,
            )

    if not evaluations:
        explanation = "No candidate images are available."
    else:
        top, top_result = evaluations[0]
        explanation = (
            f"No confident match. The top-ranked image (id {top.image_id}) "
            f"was rejected: {top_result.explanation}"
        )
    return GuardSelection(
        decision=Decision.NO_CONFIDENT_MATCH,
        chosen=None,
        evaluations=tuple(evaluations),
        explanation=explanation,
    )