# Design (one page): AI Image Understanding & Content Matching Engine

**Problem.** A content team has an image library and needs the right image for each article. Filenames and keywords pick plausible but wrong images, such as a gray wolf for a red-fox post. The system must suggest an image only when it is confident, and otherwise answer "no confident match" with reasons.

**Approach.** A vision model tags every image as schema-validated JSON (subject, category, aliases, caption, confidence, visibility). The tag text is embedded. When a post is created, a small text call extracts what the post is about, and the post is embedded. pgvector ranks images by cosine similarity. A **mismatch guard** then checks every candidate before anything is suggested:

1. the post has a concrete subject
2. the categories match
3. the post's subject appears (whole words) in the image's subject or aliases
4. the image is not flagged unclear or low-confidence
5. similarity is at or above the threshold (0.55, chosen from a labelled eval set)

All failed checks are reported, so a rejection reads like "Animal category mismatch: expected red fox, detected gray wolf".

**Data model** (PostgreSQL + pgvector, schema as Alembic migrations)

| Table | Holds |
|---|---|
| `images` | filename (unique), status `pending / processing / tagged / flagged / failed`, attempts, last_error |
| `image_metadata` | one row per image: subject, category, visibility, aliases, attributes, caption, confidence, validation_status |
| `image_vectors`, `post_vectors` | `vector(768)` plus model name, unique per (item, model), HNSW cosine index |
| `posts` | title, content, expected_subject, expected_category |
| `suggestions` | one row per (post, image): score, decision `suggested / alternative / rejected`, reason |
| `reviews` | human approve or reject per suggestion, history kept |
| `jobs`, `ai_usage` | batch-run progress; one row per AI call with model, tokens, cost |

**API surface**

- `POST /posts` and `GET /posts/{id}` create and read a post (a text call and an embedding call; 429 over budget)
- `GET /posts/{id}/images` returns ranked candidates with a guard verdict each, and either the suggestion or `no_confident_match`
- `POST /posts/{id}/check` forces one image and returns the guard's verdict
- `GET /images`, `GET /images/{id}` return images with their tags
- `GET /suggestions`, `GET /suggestions/{id}` explain why an image was chosen or refused
- `POST /suggestions/{id}/review` approves or rejects (a guard-rejected pairing returns 409)
- `GET /usage` and `GET /usage/summary` give the cost log
- `POST /jobs/process-images` (202), `GET /jobs` and `GET /jobs/{id}` run and watch the batch job
- `GET /health`

**Layers**

```
HTTP    app/api       thin routes, Pydantic request and response models, error mapping (422/404/409/429/502)
Logic   app/services  vision, post analysis, embeddings, matching, mismatch guard (pure, no I/O), cost tracking
Data    app/models    SQLAlchemy tables; Alembic migrations
Jobs    app/jobs      background batch: retries with backoff, circuit breaker, budget guard, crash recovery
```

**Non-goal.** This is not an image-management platform: no UI, upload, authentication or multi-tenancy, and no comparison of models.

**Evaluation.** 20 labelled posts (16 with one correct image, 4 with none), split into dev (choose the threshold) and test (report only). Headline metric: top-1 precision on the test split, plus the number of wrong suggestions, which must be zero.
