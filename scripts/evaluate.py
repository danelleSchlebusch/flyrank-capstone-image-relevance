"""Measure matching quality on the labelled set (eval/eval_set.json)."""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.logging_setup import setup_logging
from app.models import Image, Post
from app.services.matching import find_candidates
from app.services.mismatch_guard import GuardThresholds, PostProfile, select_best
from app.services.posts import create_post

EVAL_FILE = Path("eval/eval_set.json")


def load_items(db):
    """Create any missing posts (one text call and one embedding call each),
    then fetch every post's ranked candidates. After this, no more AI calls."""
    ids_by_name = {i.filename: i.id for i in db.scalars(select(Image))}
    items = []
    for raw in json.loads(EVAL_FILE.read_text(encoding="utf-8")):
        label = raw["correct_image"]
        if label is not None and label not in ids_by_name:
            raise SystemExit(f"{raw['id']}: no image named {label!r}")
        post = db.scalar(select(Post).where(Post.title == raw["title"]))
        if post is None:
            post = create_post(db, raw["title"], raw["content"])
            time.sleep(settings.job_delay_seconds)
        items.append({
            "id": raw["id"],
            "split": raw["split"],
            "label_id": ids_by_name[label] if label else None,
            "label_name": label,
            "subject": post.expected_subject,
            "profile": PostProfile(post.expected_subject, post.expected_category),
            "candidates": find_candidates(db, post.id, settings.match_top_k),
        })
    return items, {v: k for k, v in ids_by_name.items()}


def judge(item, min_sim):
    th = GuardThresholds(min_sim, settings.min_confidence)
    selection = select_best(item["profile"], item["candidates"], th)
    chosen = selection.chosen.image_id if selection.chosen else None
    label = item["label_id"]
    if label is None:
        outcome = "refused_ok" if chosen is None else "false_accept"
    elif chosen == label:
        outcome = "correct"
    else:
        outcome = "missed" if chosen is None else "wrong"
    return outcome, chosen, selection

def judge_threshold_only(item, min_sim):
    """Baseline without the guard: rank-1 by similarity, accepted if it clears the bar."""
    top = item["candidates"][0] if item["candidates"] else None
    chosen = top.image_id if top and top.similarity >= min_sim else None
    label = item["label_id"]
    if label is None:
        return "refused_ok" if chosen is None else "false_accept"
    if chosen == label:
        return "correct"
    return "missed" if chosen is None else "wrong"

def metrics(items, min_sim):
    c = Counter(judge(i, min_sim)[0] for i in items)
    answerable = c["correct"] + c["missed"] + c["wrong"]
    return {
        "threshold": min_sim,
        "answerable": answerable,
        "unanswerable": c["refused_ok"] + c["false_accept"],
        "correct": c["correct"], "missed": c["missed"], "wrong": c["wrong"],
        "refused_ok": c["refused_ok"], "false_accept": c["false_accept"],
        "top1_precision": round(c["correct"] / answerable, 3) if answerable else None,
    }


def pick(items, split):
    return items if split == "all" else [i for i in items if i["split"] == split]


def print_sweep(items):
    print("       |------- with guard -------|  |---- threshold only (no guard) ----|")
    print("thr    correct missed wrong | ref  FA   correct missed wrong | ref  FA")
    for step in range(30, 76):
        thr = step / 100
        g = metrics(items, thr)
        t = Counter(judge_threshold_only(i, thr) for i in items)
        print(f"{thr:.2f}   {g['correct']:>2}/{g['answerable']:<2}  {g['missed']:>4}  {g['wrong']:>4} |"
              f" {g['refused_ok']:>2}  {g['false_accept']:>2}    "
              f"{t['correct']:>2}/{g['answerable']:<2}  {t['missed']:>4}  {t['wrong']:>4} |"
              f" {t['refused_ok']:>2}  {t['false_accept']:>2}")


def print_report(items, names, min_sim):
    print(f"\nmin_similarity = {min_sim}   min_confidence = {settings.min_confidence}")
    for split in ("dev", "test", "all"):
        m = metrics(pick(items, split), min_sim)
        print(f"\n[{split}] top-1 precision: {m['correct']}/{m['answerable']} = {m['top1_precision']}"
              f"  | wrong suggestions: {m['wrong']}  | missed (safe refusals): {m['missed']}"
              f"  | refused correctly: {m['refused_ok']}/{m['unanswerable']}"
              f"  | false accepts: {m['false_accept']}")

    # Baseline: always take the single most similar image. No guard, no refusals.
    answerable = [i for i in items if i["label_id"] is not None]
    hits = sum(1 for i in answerable if i["candidates"][0].image_id == i["label_id"])
    misses = [i["id"] for i in answerable if i["candidates"][0].image_id != i["label_id"]]
    print(f"baseline wrong on: {misses}")
    print(f"\nbaseline (rank-1 by similarity only, never refuses): {hits}/{len(answerable)} correct, "
          f"{len(items) - len(answerable)}/{len(items) - len(answerable)} unanswerable posts got an image")

    print("\nposts that did not come out right:")
    for item in items:
        outcome, chosen, selection = judge(item, min_sim)
        if outcome in ("correct", "refused_ok"):
            continue
        got = names[chosen] if chosen else "no confident match"
        print(f"  [{item['split']}] {item['id']:11} {outcome:12} expected={item['label_name']} "
              f"got={got}\n      subject read as {item['subject']!r}: {selection.explanation}")
    return metrics(items, min_sim)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true", help="try thresholds 0.50-0.75 on the dev split")
    parser.add_argument("--min-sim", type=float, default=None)
    parser.add_argument("--json", default=None, help="write the final numbers to this file")
    args = parser.parse_args()

    setup_logging()
    with SessionLocal() as db:
        items, names = load_items(db)

    if args.sweep:
        print_sweep(pick(items, "dev"))
        return

    min_sim = args.min_sim if args.min_sim is not None else settings.min_similarity
    result = print_report(items, names, min_sim)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()