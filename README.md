# AI Image Understanding & Content Matching Engine

A backend service that tags an image library with a vision model and matches
images to blog posts by *meaning*. Its core feature is a **mismatch guard**: a
red-fox post gets the red-fox photo, a wolf is refused with an explanation, and
when nothing fits, the system answers "no confident match" instead of guessing.

Python · FastAPI · PostgreSQL + pgvector · Gemini free tier (no credit card).

## Design documents
`docs/design-one-page.md`
`docs/design-document.md`

## Quick start

Requires only Docker. No API key is needed to explore the seeded demo.

```bash
cp .env.example .env                       # Windows: copy .env.example .env
docker compose up --build --wait           # database + API, migrations run automatically
docker compose exec -T api python -m scripts.seed       # loads recorded tags, vectors, posts, cost log
docker compose exec -T api python -m scripts.evaluate   # prints the precision numbers below
docker compose exec -T api python -m pytest -q          # 60 tests
```

Then open http://localhost:8000/docs (interactive API) and try:

```bash
curl http://localhost:8000/posts/1/images                     # red-fox post: fox ranked first
curl -X POST http://localhost:8000/posts/1/check \
     -H "Content-Type: application/json" -d '{"filename":"wolf.jpg"}'   # wolf is rejected
curl http://localhost:8000/posts/4/images                     # penguin post: no confident match
curl http://localhost:8000/usage/summary                      # cost per operation
```

(PowerShell: use `Invoke-RestMethod`, or `curl.exe` with a JSON file.)

**Live mode** (watch the vision job run yourself): put a free Gemini key from
https://aistudio.google.com in `.env` as `GEMINI_API_KEY`, start from an empty
database (`docker compose down -v && docker compose up --build --wait`), run
`docker compose exec -T api python -m scripts.seed --images-only`, then
`curl -X POST http://localhost:8000/jobs/process-images` and poll `GET /jobs`.

Why a seed snapshot? Gemini's tags are not perfectly repeatable between runs
(see Limitations), so the seed loads the recorded run. That makes the numbers
below reproducible byte for byte.

## Architecture

```
 data/images ──ingest──▶ images (pending)
                           │   BATCH JOB (background): retries + backoff, circuit breaker,
                           │   daily budget guard, per-call cost log, crash recovery
                           ▼
                 Vision model (Gemini) ──▶ Pydantic schema validation ──▶ image_metadata
                           │                 (invalid output is never stored;
                           │                  unclear images are flagged)
                           ▼ embed description + aliases
                    image_vectors (pgvector, HNSW cosine index)

 New post ──▶ Gemini reads its subject + category ──▶ posts
          └─▶ embed post text ──▶ post_vectors

 GET /posts/{id}/images
      │  pgvector cosine ranking (top 10)
      ▼
   MISMATCH GUARD, run on every candidate:
      subject (+ aliases) · category · unclear/low confidence · similarity threshold
      │
      ├─▶ best candidate that passes the guard  ─▶ "suggested"
      └─▶ none passes ─▶ "no confident match" + a reason for each candidate
      │
      ▼
   suggestions table ─▶ Review API: approve / reject / inspect why
```

Layers: `app/api` (HTTP) → `app/services` (logic, including the guard, which has
no database or AI dependency) → `app/models` (SQLAlchemy). `app/jobs` holds the
background job. Schema changes are Alembic migrations.

## How the guard decides

A candidate is accepted only if it passes **every** check, and *all* failures are
reported, not just the first:

| Reason code | Fails when |
|---|---|
| `missing_post_subject` | The post has no concrete subject, so nothing can be verified |
| `category_mismatch` | e.g. post is about an animal, image shows a vehicle |
| `subject_mismatch` | e.g. "expected red fox, detected gray wolf". A post subject matches the image's subject or one of its aliases as whole words, so "fox" matches "arctic fox", but "red fox" does not |
| `unclear_image` / `low_confidence` | The vision model flagged the image as a texture or abstract close-up, or reported low confidence |
| `low_similarity` | Cosine similarity below the threshold (0.55) |

## Results

Labelled eval set: 20 posts (16 answerable with one correct image, 4 with no
suitable image), split into `dev` (used to choose the threshold) and `test`
(used only to report). Run `scripts.evaluate` to reproduce.

| Split | Top-1 precision | Wrong suggestions | Unanswerable posts refused |
|---|---|---|---|
| dev | 8/8 = 1.00 | 0 | 2/2 |
| **test** | **7/8 = 0.875** | 0 | 1/2 |
| all | 15/16 = 0.938 | 0 | 3/4 |

- **Headline: top-1 precision on the held-out test split is 0.875 (7/8).**
- A "most similar image wins" baseline also scores 15/16 on answerable posts, but
  it returns an image for **all 4** posts that have no right answer. The guard
  refused 3 of them, and it never suggested a wrong image on an answerable post.
- The threshold (0.55) was chosen from the dev sweep: with the guard, results are
  perfect from 0.30 to 0.59 and degrade above 0.60; 0.55 sits inside the plateau
  with margin below the cliff. Without the guard, the safe window shrinks to
  0.55–0.59. Similarity alone cannot separate right from wrong here (the best wrong
  image for the penguin post scored 0.579, above the weakest correct match), so
  the subject check does most of the rejecting.
- The two misses are explained in Limitations: `house` scored 0.549, just under the
  threshold, and `toad` was wrongly matched to a frog.

## API

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness and database check |
| `GET /images`, `GET /images/{id}` | Images with validated tags (`?status=flagged`) |
| `POST /posts`, `GET /posts` | Create a post (reads and embeds it), list posts |
| `GET /posts/{id}/images` | Ranked images, guard verdict on each, suggestion or no confident match |
| `POST /posts/{id}/check` | Force one image as a candidate and see the guard's verdict |
| `GET /suggestions`, `GET /suggestions/{id}` | Inspect why an image was selected or refused |
| `POST /suggestions/{id}/review` | Approve or reject (guard-rejected pairings cannot be approved: 409) |
| `GET /usage`, `GET /usage/summary` | Every AI call with model, item and cost; totals per operation |
| `POST /jobs/process-images`, `GET /jobs` | Start and watch the background batch job (202) |

Bad input returns a clean 4xx (422, 404, 409, 429), never a 500; upstream AI failures
return 502.

## Acceptance probe table
| Probe	| Command | Expected |
|---|---|---|
| 1 Batch job	| POST /jobs/process-images, then GET /images?status=flagged | Every image tagged; feathers, hair and unknown flagged |
| 2 Fox ranks | first	GET /posts/1/images	| fox.jpg rank 1 and suggested; wolf and wild dog lower, rejected |
| 3 Wolf rejected	| POST /posts/1/check with {"filename":"wolf.jpg"} |	rejected: “Animal category mismatch: expected red fox, detected gray wolf” |
| 4 No match | GET /posts/4/images	| no_confident_match with reasons |
| 5 Eval |	python -m scripts.evaluate | test 7/8 = 0.875 |
| 6 Cost log | GET /usage and GET /usage/summary | Every call has a model, item and cost |


## Design decisions

- **Descriptions, not pixels, are embedded.** The vision model writes a caption
  and tags; that text (subject, category, caption, attributes, aliases) is
  embedded. It is cheap, and every match can be inspected by reading the text.
- **`visibility` instead of trusting `confidence`.** The model reported 0.95–1.0
  for almost every image, including blurry texture shots, so confidence alone never
  flagged anything. The model is asked what is visible (whole subject, partial,
  texture or abstract) and texture/abstract images are flagged.
- **Aliases.** The model also lists broader true names ("puppy" for a golden
  retriever puppy), so a general post can find a specific image.
- **Idempotent by construction.** Unique constraints and existence checks mean
  re-running ingestion, tagging, embedding, seeding or matching creates no duplicates.
- **Models:** `gemini-3.1-flash-lite` (vision and post analysis) and
  `gemini-embedding-001` at 768 dimensions. Names are settings, because model names
  change; check them if a call returns 404.
- **Cost tracking:** every call is logged with model, item, tokens and cost. Prices
  are settings (0 on the free tier), with a daily budget that stops the job.

## Limitations (please read)

- **Small evaluation.** 8 answerable posts per split, so one post moves precision by
  12.5 points. The prompts were tuned after seeing the corpus, labels were written
  by one person, and 0.875 should not be read as a precise estimate.
- **Model confidence is nearly useless here.** See above; flagging relies on
  `visibility`.
- **Tags are not perfectly repeatable.** Re-tagging the same image changed some
  subjects (e.g. "rabbit" became "marsh rabbit"), even at temperature 0. This is why
  the seed snapshot exists.
- **Alias errors cause false accepts.** The model sometimes lists a wrong alias:
  `frog` got "toad", so a toad post is matched to the frog (the one false accept in
  the eval). `cheetah`, `lion` and `leopard` carry "cat", `red deer` carries "elk",
  and an African wild dog matches a "dog" post. These cannot be fixed by a
  threshold.
- **One subject per image.** `fish.jpg` is a reef scene dominated by a soft coral
  with a swarm of small fish; it is tagged as coral, so a "fish" post finds nothing.
- **Subject matching is word-based.** "fox" matches "arctic fox" by design; for a
  general post, only similarity ranking chooses between the two.
- **Single worker.** The job runs inside the API process and assumes one job at a
  time (enforced). Interrupted work is recovered at startup. A call that was paid for
  but whose result was lost in a crash is not recorded in the cost log. Embedding
  cost is estimated from text length because the API reports no token count.
- **Not production hardened.** No authentication, no multi-tenancy, containers run as
  root. These are explicit non-goals, along with a UI, image upload, and model
  comparison.
- **Whole-word matching.** Lion matches sea lion, fox matches flying fox.
- **POST /posts.** isn’t idempotent.

## Project layout

```
app/api        HTTP routes            app/models    SQLAlchemy tables
app/services   guard, vision, matching app/schemas   Pydantic schemas
app/jobs       batch job              alembic/      migrations
scripts/       seed, evaluate, export_snapshot, run_tagging_job, try_*
eval/          labelled eval set      data/images   52 images (credits: data/SOURCES.md)
data/seed      recorded snapshot      tests/        60 tests
docs/          design document        EVIDENCE.md   proof for each requirement
```

## License

MIT. Images are from Unsplash and Pexels; credits are in `data/SOURCES.md`.