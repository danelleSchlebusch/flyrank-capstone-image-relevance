# AI Image Understanding & Content Matching Engine

**Design Document · Revision 3 (as built) · 21 October 2026**

> **How to read this revision.** Revision 2 (7 October 2026) was the plan. This revision describes the system as it was built and measured. Where the build differed from the plan, the build is documented here, because the numbers in the README and EVIDENCE.md come from it. Appendix A lists what changed and why. Appendix B lists what was planned and not built. A one-page summary is in `design-one-page.md`.

---

## 1. Problem

A content team has a library of images and needs the right image for each article. Filenames, keywords and manual choice lead to wrong recommendations. The worst failure is a visually similar but wrong image, such as a gray wolf suggested for a red-fox post: it looks plausible and it is wrong.

This service works out what each image shows, stores that as validated structured metadata and an embedding, and ranks images for a post by meaning. It then applies a **mismatch guard** that refuses wrong or uncertain matches with a human-readable reason. If no image is good enough, it answers "no confident match" instead of guessing.

The guard compares two facts that come from two different AI calls: what the **image** shows (vision model) and what the **post** is about (a small text call made when the post is created). Both use the same category list and the same naming rule (common English name, lower case, singular), so they can be compared directly.

## 2. Goals

The system will:

- Ingest an image library of at least 40 images in at least 4 categories (built: 52 images, 5 categories).
- Tag each image with a vision model, producing Pydantic-validated metadata. Invalid output is never stored. Unclear images are flagged, never accepted silently.
- Extract each post's expected subject and category when the post is created.
- Embed image descriptions and posts, and rank images by cosine similarity.
- Apply a mismatch guard to every candidate, and explain every rejection.
- Answer "no confident match", with reasons, when no candidate passes.
- Run slow, bulk AI work as a background batch job with retries, progress tracking, failure records and an alert.
- Make reprocessing idempotent, so repeating an operation never creates duplicate rows.
- Record every AI call with its model, size and estimated cost, and enforce a daily cost limit.
- Provide an API to retrieve suggestions and to approve, reject and inspect them.
- Measure quality on a labelled evaluation set, with the threshold chosen on one part of the data and results reported on a held-out part.

## 3. Non-Goals

- No image-management platform and no frontend. Endpoints and tables are the interface.
- No external queue or broker (Redis, Celery, RabbitMQ). The batch job runs inside the API process, one run at a time.
- No automatic learning from reviews. Reviews are recorded for audit and do not change thresholds.
- No authentication, accounts or multi-tenancy.
- No comparison of multiple vision or embedding models.
- No upload endpoint and no near-duplicate detection. The corpus is a fixed folder of files, unique by filename.
- No guarantee that the vision model tags the same image identically on every run (see section 14).

## 4. Architecture

```
 data/images ──ingest──▶ images (pending)
                           │   BATCH JOB (background, one at a time): retries with backoff,
                           │   circuit breaker, daily budget guard, crash recovery, alert
                           ▼
                 Vision model ──▶ Pydantic validation ──▶ image_metadata
                           │        (invalid output is never stored;
                           │         texture or abstract images are flagged)
                           ▼ embed description + aliases
                    image_vectors (pgvector)

 POST /posts ──▶ budget check ──▶ text call: subject + category ──▶ posts
                              └─▶ embed post text ───────────────▶ post_vectors
                                  (one transaction: both calls succeed or nothing is saved)

 GET /posts/{id}/images
      │  pgvector cosine ranking (top K = 10)
      ▼
   MISMATCH GUARD on every candidate
      │
      ├─▶ best candidate that passes ──▶ "suggested"  (other passing ones: "alternative")
      └─▶ none passes ──▶ "no confident match" + a reason for every candidate
      │
      ▼
   suggestions table ──▶ Review API: approve / reject / inspect why
```

Matching reads only stored data, so it makes no AI call at request time.

**Layers.**

| Layer | Package | Responsibility |
|---|---|---|
| HTTP | `app/api` | Thin routes, request and response models, mapping errors to status codes |
| Logic | `app/services` | Vision, post analysis, embeddings, matching, the guard (pure code, no database or network), cost tracking, retry helper |
| Data | `app/models`, `alembic/` | SQLAlchemy tables; schema changes only through migrations |
| Jobs | `app/jobs` | The batch job |

**Stack.** Python, FastAPI, Pydantic, SQLAlchemy 2 with Alembic, PostgreSQL 16 with pgvector, Gemini (`gemini-3.1-flash-lite` for vision and post analysis, `gemini-embedding-001` at 768 dimensions), Docker Compose.

## 5. Data Model

All timestamps are `timestamptz`. Embeddings are `vector(768)`. The size is fixed in the schema, so changing it needs a migration; the code checks every returned vector has exactly this length.

**images** — the source image and its processing state.

| Field | Type | Purpose |
|---|---|---|
| id | serial PK | |
| filename | text, UNIQUE | Identity of the file. Registering a file twice is a no-op |
| image_url | text | Where the file is served from |
| status | text | `pending`, `processing`, `tagged`, `flagged`, `failed` |
| attempts, last_error | int, text | How many tries the job made, and the latest failure |
| created_at | timestamptz | |

**image_metadata** — validated vision output. One row per image.

| Field | Type | Purpose |
|---|---|---|
| image_id | FK, UNIQUE | One metadata row per image |
| subject | text | Common English name, lower case (e.g. `red fox`) |
| category | text | `animal`, `vehicle`, `building`, `furniture`, `electronics`, `other` |
| visibility | text | `whole_subject`, `partial_subject`, `texture_or_abstract` |
| aliases | jsonb | Other true names: broader (`dog` for a puppy) or short (`hippo`) |
| attributes | jsonb | Short visual descriptors |
| caption | text | One factual sentence |
| confidence | float | The model's self-reported certainty, 0 to 1 |
| validation_status | text | `valid` or `low_confidence` (set when visibility is `texture_or_abstract` or confidence is below the minimum) |

**image_vectors** and **post_vectors** — embeddings. Each has the item id, `embedding vector(768)`, `model`, and a UNIQUE constraint on (item id, model), so a different embedding model never overwrites an existing vector.

**posts** — `title`, `content`, `expected_subject` (null when the post has no concrete subject), `expected_category`, `created_at`. The category list is the image list plus `unclear`, which is stored as null.

**suggestions** — one row per evaluated (post, image) pair, UNIQUE (post_id, image_id). Holds `similarity_score`, `decision` (`suggested`, `alternative`, `rejected`) and the human-readable `reason`. Re-running matching updates these rows in place.

**reviews** — human decisions: `suggestion_id`, `decision` (`approved`, `rejected`), optional `comment`. The history is kept; the latest review is the current state.

**jobs** — one row per batch run: `type`, `status` (`queued`, `running`, `completed`, `stopped`), `total`, `succeeded`, `flagged`, `failed`, `stop_reason`, and three timestamps.

**ai_usage** — one row per AI call: `operation` (`vision`, `embedding`, `text`), `model`, `item_id`, `tokens_or_units`, `cost` (`numeric(12,6)`), `created_at`.

### 5.1 Indexes

Every index below exists in the migrations.

| Table | Index | Supports |
|---|---|---|
| images | UNIQUE (filename) | Idempotent registration |
| images | (status) | Finding pending or failed work, status counts |
| image_metadata | UNIQUE (image_id) | One row per image |
| image_metadata | (category) | Filtering and reporting by category |
| image_vectors | UNIQUE (image_id, model); (image_id) | Idempotent writes; foreign key lookup |
| image_vectors | HNSW (embedding vector_cosine_ops), m=16, ef_construction=64 | Nearest-neighbour search |
| post_vectors | UNIQUE (post_id, model); (post_id) | Idempotent writes; foreign key lookup |
| post_vectors | HNSW (embedding vector_cosine_ops) | Nearest-neighbour search on posts |
| suggestions | UNIQUE (post_id, image_id); (post_id); (image_id); (decision) | Idempotent re-matching; foreign keys; review queue |
| reviews | (suggestion_id) | Foreign key lookup |
| jobs | (status) | Finding the active job |
| ai_usage | (created_at); (operation) | Daily budget sum; cost by operation |

At about 50 vectors PostgreSQL will normally choose a sequential scan, because that is faster than an index at this size. The HNSW indexes exist so the design scales without a schema change.

## 6. Image Understanding

### 6.1 The schema the vision model must satisfy

```json
{
  "subject": "red fox",
  "category": "animal",
  "visibility": "whole_subject",
  "aliases": ["fox"],
  "attributes": ["orange fur", "wild", "forest"],
  "caption": "A red fox standing in a forest",
  "confidence": 0.94
}
```

The model is asked for structured JSON, and the response is validated again by Pydantic before anything is stored. Category and visibility must be one of the listed values, confidence must be between 0 and 1, and text fields are length-limited and normalised.

### 6.2 Flagging unclear images: why `visibility`, not only `confidence`

The original plan flagged images with low `confidence`. In practice the model reported 0.95 to 1.0 for almost every image, including a macro shot of fur and a close-up of feathers, so confidence alone flagged nothing. The model is therefore also asked *what is visible*: the whole subject, part of it, or only a texture or abstract pattern. An image with visibility `texture_or_abstract` gets `validation_status = low_confidence` and status `flagged`. A flagged image keeps its metadata and its vector, and the guard rejects it with an `unclear_image` reason. Flagged images are ranked but are never suggested. Confidence remains a second check (below 0.60 also flags an image), but it is a weak signal and the README says so.

### 6.3 Status lifecycle

`pending` → `processing` → `tagged` or `flagged`; or `failed` (the model's output was invalid, or a permanent error occurred); or back to `pending` after a temporary error. Nothing validated is overwritten, and an image that already has metadata is skipped.

### 6.4 Aliases

A post about "bears" should find an image tagged `black bear cub`, and a post about "dogs" one tagged `golden retriever puppy`. The model lists up to a few other names that are true of the subject. The guard accepts a post subject that matches the image's subject or any alias. Aliases are model output and sometimes wrong (section 14), and they are also added to the text that gets embedded, which helps recall.

## 7. Post Analysis

When `POST /posts` creates a post, the service checks the daily budget, then makes one text-only call using the title and the first 2,000 characters of the content. The response must validate against:

```python
class PostSubject(BaseModel):
    subject: str       # common English name, lower case, singular; "" if there is no concrete subject
    category: Literal["animal", "vehicle", "building", "furniture",
                      "electronics", "other", "unclear"]
```

The prompt tells the model to convert scientific names and descriptions to the common name, so "Vulpes vulpes" becomes `red fox`, and to keep a specific kind when the post names one (`red foxes` becomes `red fox`). The post is then embedded and everything is saved in **one transaction**: if either AI call fails, nothing is saved.

| Outcome | Effect |
|---|---|
| Subject found | The guard runs normally |
| Empty subject (opinion, advice, abstract topic) | `expected_subject` is null; every candidate is rejected with `missing_post_subject`, so the answer is "no confident match" |
| AI call fails after retries | 502; the post is not saved |
| Daily budget reached | 429; the post is not saved |

The design fails closed: if the system cannot tell what a post is about, it does not guess an image.

## 8. Matching and the Mismatch Guard

### Stage 1: semantic ranking

The post vector is compared with every stored image vector using cosine distance in pgvector, ordered by distance and limited to the top K (default 10); similarity is `1 - distance`. Images use the embedding task type `RETRIEVAL_DOCUMENT` and posts use `RETRIEVAL_QUERY`. Ranking never produces a recommendation on its own.

### Stage 2: the guard

The guard evaluates **every** candidate and records **all** failed checks, not just the first. A candidate with no failures passes.

| Check | Fails when | Reason code |
|---|---|---|
| Post subject known | The post has no concrete subject | `missing_post_subject` |
| Category | Post category and image category differ (skipped when the post category is `unclear`) | `category_mismatch` |
| Subject | The post's subject does not match (rule below). Only evaluated when the category matches | `subject_mismatch` |
| Image clear | Confidence is below `MIN_CONFIDENCE`, or the image is flagged unclear | `low_confidence` or `unclear_image` |
| Similarity | Cosine similarity is below `MIN_SIMILARITY` | `low_similarity` |

**Subject rule.** Both sides are normalised: lower case, hyphens turned into spaces, and the last word singularised (`red foxes` → `red fox`, `bee-eater` → `bee eater`). The post's subject matches if it appears, as whole words, in the image's subject or in any of its aliases. So `fox` matches `arctic fox`, and `black bear` matches `black bear cub`, but `red fox` does not match `arctic fox` or `gray wolf`. The rule is asymmetric on purpose: a specific post must not accept a more generic image, but a general post accepts any kind of that animal. Which of several matching images comes first is decided by similarity.

**Decision.** The best-ranked candidate that passes is `suggested`. Other passing candidates are `alternative` and the rest are `rejected`. If none passes, the answer is `no_confident_match`, with the top candidate's reasons in the explanation.

**Worked example (real output, post "How red foxes survive the winter", expected subject `red fox`).**

| Rank | Image | Similarity | Result |
|---|---|---|---|
| 1 | fox.jpg (`red fox`) | 0.673 | suggested |
| 2 | arctic_fox.jpg | 0.665 | rejected: expected red fox, detected arctic fox |
| 3 | squirrel.jpg (`red squirrel`) | 0.649 | rejected: subject mismatch |
| 10 | wolf.jpg (`gray wolf`) | 0.549 | rejected: subject mismatch and similarity below 0.550 |

The arctic fox and the red squirrel are the dangerous candidates. They are only slightly less similar than the right answer, and similarity alone cannot tell them apart. The subject check does. `POST /posts/{id}/check` lets a reviewer force any image as a candidate and see the guard's verdict without saving anything.

### Thresholds

`MIN_SIMILARITY = 0.55` and `MIN_CONFIDENCE = 0.60`. The initial 0.70 was a placeholder and rejected every correct match for paragraph-length posts (correct matches scored 0.61 to 0.67). The final value comes from a sweep over the dev split (section 12): with the guard, results are perfect for every threshold from 0.30 to 0.59 and degrade above 0.60. 0.55 sits inside that range with a margin below the cliff.

### Failure behaviour

| Failure | Result |
|---|---|
| Image output invalid | Image is `failed` and never a candidate |
| Image unclear or low confidence | Ranked, rejected with a reason, never suggested |
| Post vector missing | 409. An image without a vector is simply not ranked |
| Post subject empty | All candidates rejected; `no_confident_match` |
| Daily budget reached | New AI work is refused (429) or stops the job. Matching continues on stored data |

## 9. Background Processing

**Decision.** The batch job is a function started with FastAPI `BackgroundTasks`, with progress in a `jobs` table. At about 50 images this gives retries, progress and failure records with no infrastructure beyond PostgreSQL. A separate worker process with a per-item queue was considered (Appendix B) and not built.

**Run.** `POST /jobs/process-images` registers any new files, creates a job (409 if one is already queued or running) and returns 202 immediately. The job selects images that still need work: `pending` ones (and `failed` ones with `retry_failed=true`), plus `tagged` or `flagged` images that have no vector yet. For each image it checks the budget, tags the image, embeds its description, and commits progress, so an interrupted run resumes where it stopped.

| Situation | Behaviour |
|---|---|
| Temporary error (network, 408, 429, 5xx) | Retry with exponential backoff and jitter: base 3 s, up to 4 attempts. Embedding calls retry the same way |
| Permanent error (bad key, bad request) | No retry; the image is `failed` with `last_error` |
| Model output fails schema validation | Not retried; the image is `failed`. The call is still recorded in `ai_usage` because it was billed |
| 3 images fail in a row | The job stops (circuit breaker) with a stop reason, to avoid wasting calls on a systemic problem |
| Daily budget reached | The job stops with a stop reason |
| Process or database crash | Image stays `processing` and the job stays `running`. On the next start, recovery resets stuck images to `pending` and marks stale jobs `stopped` |
| Job stopped, failed or crashed | An `ERROR` log line, plus a POST to `ALERT_WEBHOOK_URL` if configured (exception text is never logged there, because it could contain the URL) |

Recovery assumes a single worker. Running several jobs in parallel would need a heartbeat check instead.

## 10. Idempotency and Cost Control

### 10.1 Idempotency

| Scenario | Guarantee | Mechanism |
|---|---|---|
| Files registered twice | One `images` row each | Existence check, backed by UNIQUE (filename) |
| Image tagged again | Metadata written once | Skipped if metadata exists; UNIQUE (image_id) |
| Image embedded again | One vector per model | Existence check, backed by UNIQUE (image_id, model) |
| Job started twice | No parallel runs | One active job at a time (409) |
| Job re-run after a stop | Only remaining work is done | Selection by status and missing vectors |
| Matching run again | One suggestion per (post, image) | Upsert on UNIQUE (post_id, image_id); human reviews stay attached |
| Review submitted twice | Same review returned | An identical repeat returns 200; a changed decision or comment adds a history row (201) |
| Demo data seeded twice | No duplicates | Every insert in the seed is guarded by an existence check |

`POST /posts` is deliberately **not** idempotent: two posts with the same text are two posts, and each costs two AI calls. A retried AI call is a real second call and gets its own `ai_usage` row.

### 10.2 Cost tracking and the budget guard

Every AI call writes an `ai_usage` row. Vision cost is computed from the token counts the API returns, multiplied by prices in configuration (zero on the free tier, but the mechanism is real). Embedding calls return no token count, so their size is estimated from text length (about four characters per token) and labelled as an estimate.

`DAILY_BUDGET_USD` is checked before every batch-job image and before `POST /posts`. The total is the sum of today's `ai_usage.cost` since 00:00 UTC. A single call can overshoot the limit by its own cost. A call that was paid for but whose result was lost in a crash is not recorded. `GET /usage/summary` shows spend by operation, spend today and the limit; `GET /usage` lists every call.

## 11. API Surface

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness and database check |
| `GET /images`, `GET /images/{id}` | Images with validated tags; `?status=` filter; pagination |
| `POST /posts` | Create a post: budget check, subject extraction, embedding (201) |
| `GET /posts`, `GET /posts/{id}` | List and read posts |
| `GET /posts/{id}/images` | Ranked candidates with a verdict each, the suggestion or `no_confident_match`; stores the suggestions |
| `POST /posts/{id}/check` | Body `{"image_id": n}` or `{"filename": "..."}`: the guard's verdict for one forced image; saves nothing |
| `GET /suggestions`, `GET /suggestions/{id}` | Why an image was selected or refused, with review history |
| `POST /suggestions/{id}/review` | Approve or reject. 409 if the guard rejected the pairing and the review is an approval |
| `GET /usage`, `GET /usage/summary` | Every AI call with model, item and cost; totals and today's spend |
| `POST /jobs/process-images` | Start the batch job in the background (202; 409 if one is active) |
| `GET /jobs`, `GET /jobs/{id}` | Job progress |

**Error handling.** Request validation failures return 422, unknown records 404, state conflicts 409, an exhausted budget 429, an AI provider failure after retries 502 (detail logged, not returned), an unreachable database 503, and anything unexpected a generic 500 body. Bad input never reaches a 500.

## 12. Quality and Evaluation

**Data.** `eval/eval_set.json` holds 20 posts: 16 with exactly one correct image and 4 with none (a motorcycle, penguins, toads, and an opinion piece with no concrete subject). Each post is marked `dev` or `test`. The posts include a Latin-name post (*Vulpes vulpes*) that tests concept matching, and deliberately leave out subjects whose images are near-duplicates (deer, dog, cat), where no single answer is correct. Run it with `python -m scripts.evaluate` (`--sweep` tries thresholds 0.30 to 0.75 on dev).

**Method.** The threshold is chosen on `dev` only. `test` is reported and never used for tuning. The sweep also runs a baseline with no guard (take the most similar image, accept it if it clears the threshold).

**Metrics.**

| Metric | Definition |
|---|---|
| Top-1 precision (primary) | Of the answerable posts, the share whose suggested image is the labelled one. A safe refusal counts as a miss |
| Wrong suggestions | Answerable posts that received a wrong image. The target is zero |
| False accepts | Posts with no right answer that received an image |
| Refusal rate | Posts with no right answer that correctly got "no confident match" |

**Results at `MIN_SIMILARITY = 0.55`.**

| Split | Top-1 precision | Wrong suggestions | Refused correctly |
|---|---|---|---|
| dev | 8/8 = 1.00 | 0 | 2/2 |
| **test** | **7/8 = 0.875** | 0 | 1/2 |
| all | 15/16 = 0.938 | 0 | 3/4 |

- A baseline that always takes the most similar image also scores 15/16 on answerable posts, but it gets the Latin-name post wrong, and it returns an image for all 4 posts that have no right answer.
- Without the guard, the safe threshold window on dev is 0.55 to 0.59. With it, 0.30 to 0.59.
- The two misses are `house` (similarity 0.549, just under the threshold, a safe refusal) and `toad` (the vision model gave the frog image the alias `toad`; no threshold can fix that).
- The sample is small: one post moves precision by 12.5 points. Prompts were adjusted after seeing the corpus, and the labels were written by one person.

**The fox and wolf probe.** (1) A forced check of `wolf.jpg` against the red-fox post returns `rejected` with "Animal category mismatch: expected red fox, detected gray wolf". (2) With `fox.jpg` ignored, the other candidates include images that clear the similarity threshold (the arctic fox and the red squirrel) and every one of them is rejected, so no wrong image is suggested. The wolf itself ranks last on similarity for this post, so it is the arctic fox and the squirrel that test the subject check. Both are automated tests.

## 13. Configuration

Every key is listed in `.env.example`. Secrets are never committed.

| Key | Default | Purpose |
|---|---|---|
| `DATABASE_URL`, `GEMINI_API_KEY` | | Connection and key |
| `VISION_MODEL`, `TEXT_MODEL`, `EMBEDDING_MODEL` | `gemini-3.1-flash-lite`, `gemini-3.1-flash-lite`, `gemini-embedding-001` | Model names |
| `MIN_SIMILARITY`, `MIN_CONFIDENCE`, `MATCH_TOP_K` | 0.55, 0.60, 10 | Guard and ranking |
| `VISION_INPUT_PRICE_PER_M`, `VISION_OUTPUT_PRICE_PER_M`, `EMBEDDING_PRICE_PER_M` | 0.0 | Cost per million tokens (USD) |
| `DAILY_BUDGET_USD` | 1.0 | Daily limit |
| `JOB_MAX_ATTEMPTS`, `JOB_BACKOFF_BASE_SECONDS`, `JOB_DELAY_SECONDS`, `JOB_MAX_CONSECUTIVE_FAILURES` | 4, 3.0, 1.0, 3 | Retry and circuit breaker |
| `ALERT_WEBHOOK_URL` | empty | Optional failure alert |

## 14. Limitations and Risks

- **Model confidence is nearly useless here.** See section 6.2.
- **Tags are not perfectly repeatable.** Re-tagging changed some subjects (for example `rabbit` became `marsh rabbit`) even at temperature 0. The seed loads a recorded run so results reproduce exactly, and results should always be measured against stored tags.
- **Alias errors cause false accepts.** Examples seen: `frog` listed as `toad`, `red deer` as `elk`, the big cats as `cat`, an apartment building as `skyscraper`. An African wild dog also matches a post about a dog, because its name ends in "dog".
- **Whole-word matching is crude.** `fox` also matches `flying fox`, `lion` matches `sea lion`, and `pig` matches `guinea pig`.
- **One subject per image.** A reef scene with a dominant coral and many small fish is tagged as the coral, so a post about fish finds nothing.
- **Similarity cannot separate right from wrong on this corpus.** The best wrong image for a post about penguins scored higher than the weakest correct match. The subject check does most of the rejecting.
- **Small, single-author evaluation.** See section 12.
- **Single worker.** One active job; recovery assumes no concurrent workers.
- **Cost log gaps.** A paid call lost in a crash is not recorded, and embedding cost is estimated.
- **Not production hardened.** No authentication, containers run as root, and `POST /posts` is not idempotent.

## 15. Definition of Done (as built)

1. Images are processed by a background batch job (`POST /jobs/process-images`).
2. Vision output is schema-validated; invalid output is never stored.
3. Unclear images are flagged and never suggested.
4. Posts get an expected subject and category when they are created.
5. Image and post embeddings are stored.
6. Posts return ranked image suggestions.
7. The mismatch guard rejects wrong images, and the fox and wolf probe passes.
8. Every rejection has a human-readable explanation, with reason codes in the API responses.
9. "No confident match" is returned, with reasons, when no candidate passes.
10. Jobs show progress, retry with backoff, keep failure records and raise an alert.
11. Reprocessing creates no duplicate rows.
12. Every AI call has an `ai_usage` row, and the daily budget is enforced before AI work.
13. The indexes in section 5.1 exist in the migrations.
14. The review workflow can approve, reject and inspect suggestions.
15. The evaluation script reports top-1 precision, wrong suggestions and refusals, and the README quotes the same numbers.
16. The repository contains README, EVIDENCE.md, BUILDLOG.md, capstone.yaml, `.env.example` and a licence, and runs from a clean clone with `docker compose up --build --wait` plus a seed step.

---

## Appendix A: Changes from Revision 2

| Topic | Revision 2 | As built | Why |
|---|---|---|---|
| Flagging unclear images | By confidence below 0.70 | By `visibility`, plus confidence below 0.60 | The model reported 0.95 to 1.0 for almost everything |
| Subject comparison | Symmetric: all words of the shorter name appear in the longer | The post's phrase must appear as whole words in the image subject or an alias | The first version (last word only) wrongly rejected about 1 in 5 images, such as `black bear cub` for a bear post. Aliases fixed it |
| Image statuses | `ready`, `needs_review` | `tagged`, `flagged` | Naming |
| Flagged images | Excluded from matching | Ranked but never suggested, with an `unclear_image` reason | Makes the refusal visible and explainable |
| Categories | Nine values | Six, plus `unclear` for posts | Fits the corpus; five of six appear in it |
| Post extraction | Nullable subject, confidence, status column and retry job | Done inside `POST /posts`; an empty subject means unclear; failure returns 502 and saves nothing | Simpler, and it still fails closed |
| Suggestions | `rank`, `reason_code`, `guard_checks` columns; `suggested` or `rejected` | Rank computed per request; reason text stored; adds `alternative` | Less to store; the API returns codes |
| Reviews | One per suggestion; a repeat returns 409 | History kept; an identical repeat returns 200 | Keeps an audit trail and is idempotent |
| Image identity | `content_hash` UNIQUE; `POST /images` | UNIQUE filename; files read from a folder | Fixed corpus |
| Jobs | Per-item queue, worker process, `SKIP LOCKED`, reaper, `run_after` backoff | One row per run, `BackgroundTasks`, in-process backoff, recovery at startup | Right size for about 50 images |
| Budget exhausted | Jobs deferred to midnight without using an attempt | The run stops with a reason; `POST /posts` returns 429 | Simpler; the run is resumable |
| Atomicity | Metadata, vector and status in one transaction | Tagging commits first, embedding second; the job picks up the gap | Two separate AI calls with separate failure modes |
| Threshold | `SIMILARITY_THRESHOLD = 0.70`, `TOP_K = 5` | `MIN_SIMILARITY = 0.55`, `MATCH_TOP_K = 10` | Chosen from the dev sweep |
| Fox and wolf probe | The wolf should rank high on similarity | The wolf ranks last for this post; the arctic fox and squirrel test the subject check | Claim checked against real data and found false |
| Evaluation | `eval/dataset.json`, `python -m eval.run`, extraction accuracy | `eval/eval_set.json`, `python -m scripts.evaluate`; dev and test split; baseline | The split guards against tuning on the data being reported |
| Embedded text | Caption | Subject, category, caption, attributes and aliases | Better recall |
| Check 1 "image usable" | Separate guard check on `ready` images | Merged into the clarity check, since flagged images are kept in ranking | The Revision 2 check could never fail |
| Category `other` | Always a mismatch | Compared like any other category | Avoids a misleading reason |
| API paths | `/reviews`, `/jobs/images/process`, `/jobs/batches/{id}`, `/usage/today` | `/suggestions`, `/jobs/process-images`, `/jobs/{id}`, `/usage/summary` | Matches the README |
| Daily-spend SQL | `date_trunc('day', now() AT TIME ZONE 'UTC')` | Computed in UTC in code | The SQL compares a timestamp with timezone to one without and is read in the session time zone |

## Appendix B: Planned and not built

- Content-hash de-duplication and `POST /images`.
- A separate worker process with a per-item jobs table, `FOR UPDATE SKIP LOCKED` claiming and a reaper.
- Post-extraction status, confidence column and a retry job.
- Stored `guard_checks` and `reason_code` columns on suggestions.
- A subject-extraction accuracy metric.
- Retrying schema-invalid model output. Revision 2 argued this is worthwhile because output varies between calls, and the observed variation supports that argument. As built, invalid output fails the image.
- A GIN index on `attributes`, partial indexes on `jobs`, and `raw_response` storage.
- `GET /usage/today`, replaced by `GET /usage/summary`.
