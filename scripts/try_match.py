import argparse

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.logging_setup import setup_logging
from app.models import Image, Post
from app.services.matching import match_post
from app.services.mismatch_guard import Decision, GuardThresholds
from app.services.posts import create_post

POSTS = [
    ("How red foxes survive the winter",
     "Red foxes stay active all winter, hunting mice under the snow by listening for "
     "movement and pouncing from above. Their thick coats and bushy tails keep them warm "
     "as they roam the forest edge at dawn and dusk."),
    ("Why do wolves howl?",
     "Gray wolves howl to keep their pack together and to warn rival packs away from "
     "their territory. A chorus of howls can carry for kilometres across forest and tundra."),
    ("Bears get ready for hibernation",
     "In autumn, black bears eat huge amounts of food to build up fat. A mother and her "
     "cubs then settle into a den to sleep through the winter."),
    ("Penguin colonies in Antarctica",
     "Emperor penguins huddle together on the ice to survive temperatures far below "
     "freezing, taking turns to stand in the warm centre of the group."),
    ("The secret life of toads",
     "Toads spend most of their lives on land and only return to ponds to breed. Their "
     "dry, bumpy skin helps them hold on to moisture."),
]

parser = argparse.ArgumentParser()
parser.add_argument("--min-sim", type=float, default=settings.min_similarity)
args = parser.parse_args()

setup_logging()
thresholds = GuardThresholds(args.min_sim, settings.min_confidence)
print(f"min_similarity = {thresholds.min_similarity}")

with SessionLocal() as db:
    names = {i.id: i.filename for i in db.scalars(select(Image))}
    for title, content in POSTS:
        post = db.scalar(select(Post).where(Post.title == title)) or create_post(db, title, content)
        selection = match_post(db, post.id, thresholds)

        print(f"\n{title}")
        print(f"  expected: {post.expected_subject!r} / {post.expected_category}")
        if selection.chosen:
            print(f"  -> SUGGESTED {names[selection.chosen.image_id]}")
        else:
            print("  -> NO CONFIDENT MATCH")
        for cand, result in selection.evaluations[:5]:
            mark = "ok" if result.decision == Decision.SUGGESTED else "no"
            print(f"     [{mark}] {cand.similarity:.3f} {names[cand.image_id]:18} {result.explanation}")