# Evidence

One proof per requirement, with real output. Everything was run on Windows 11 with Python 3.14.6, Docker Desktop, PostgreSQL 16 with pgvector, and the Gemini free tier. The API runs on `http://localhost:8000`. Evidence was collected between 13 and 21 October 2026.

## Index
 
### Section 6 requirements
 
| Requirement | Proof |
|---|---|
| Vision output validated against a schema; invalid responses never trusted | [Schema validation](#vision-output-is-schema-validated-invalid-responses-are-never-trusted) |
| Low-confidence classifications flagged, not accepted | [Flagging](#low-confidence-classifications-are-flagged-not-accepted) |
| Images processed through a batch job with retries | [Batch job](#images-are-processed-through-a-batch-background-job-with-retries) |
| Vision and embedding costs tracked per call | [Cost tracking](#vision-and-embedding-costs-are-tracked-per-call) |
| Embeddings stored; posts return ranked suggestions | [Ranking](#image-and-post-embeddings-are-stored-posts-return-ranked-suggestions) |
| "red fox" matches "Vulpes vulpes" | [Latin name](#red-fox-matches-vulpes-vulpes) |
| Wolf-on-a-fox-post provably fails | [Wolf rejected](#the-wolf-on-a-fox-post-scenario-provably-fails) |
| Rejections include a human-readable explanation | [Explanation](#rejections-include-a-human-readable-explanation) |
| "No confident match" with reasons | [No match](#no-confident-match-with-reasons) |
| Database models with required indexes | [Database](#database-models-with-the-required-indexes) |
| API validated; review workflow exists | [API and review](#api-validation-and-the-review-workflow) |
| Top-1 precision measured, same number as README | [Evaluation](#top-1-precision-is-measured-and-matches-the-readme) |
| README and required files present | [Files](#readme-and-required-files-are-present) |
 
### Acceptance probes
 
| Probe | Proof |
|---|---|
| 1. Batch job tags every image, low-confidence flagged | [Batch job](#images-are-processed-through-a-batch-background-job-with-retries) and [Flagging](#low-confidence-classifications-are-flagged-not-accepted) |
| 2. Fox post ranks the fox first | [Ranking](#image-and-post-embeddings-are-stored-posts-return-ranked-suggestions) |
| 3. Forced wolf is rejected | [Wolf rejected](#the-wolf-on-a-fox-post-scenario-provably-fails) |
| 4. Post with no image gives "no confident match" | [No match](#no-confident-match-with-reasons) |
| 5. Eval script reports precision matching the README | [Evaluation](#top-1-precision-is-measured-and-matches-the-readme) |
| 6. Cost log attributes every call | [Cost tracking](#vision-and-embedding-costs-are-tracked-per-call) |

## AI processing

### Vision output is schema-validated; invalid responses are never trusted
The schema is `ImageTags` in `app/schemas/image.py`. Unit tests feed it bad payloads (an unknown category, a confidence of 94, an unknown visibility, empty attributes, an empty subject, a too-short caption, a missing field) and each is rejected. Excerpt of `python -m pytest -v`:

```
tests/test_schemas.py::test_valid_payload_is_accepted_and_normalised PASSED                                               [ 11%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[category-wildlife] PASSED                                       [ 22%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[confidence-94] PASSED                                           [ 33%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[confidence--0.1] PASSED                                         [ 44%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[visibility-blurry] PASSED                                       [ 55%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[attributes-value4] PASSED                                       [ 66%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[subject-] PASSED                                                [ 77%]
tests/test_schemas.py::test_invalid_payloads_are_rejected[caption-hi] PASSED                                              [ 88%]
tests/test_schemas.py::test_missing_field_is_rejected PASSED                                                              [100%]

====================================================== 9 passed in 0.93s =======================================================
```

A real validated response (`python -m scripts.try_vision fox`, excerpt from the run with five images):
```
--- fox ---
{
  "subject": "red fox",
  "category": "animal",
  "aliases": [
    "fox",
    "mammal",
    "wild animal"
  ],
  "visibility": "partial_subject",
  "attributes": [
    "reddish-orange fur",
    "pointed ears",
    "alert expression",
    "outdoor setting",
    "soft lighting"
  ],
  "caption": "A close-up portrait of a red fox looking towards the side in a natural environment.",
  "confidence": 1.0
}
status: valid | tokens in/out: 1462 131
```

The model's answer is validated by `ImageTags.model_validate_json` even though Gemini is also asked for structured output. A response that fails validation raises `InvalidModelOutput`, the image becomes `failed`, and nothing is stored.

### Low-confidence classifications are flagged
The model reported a confidence of 0.95 for a macro photo of fur, so confidence alone could not flag it. The vision prompt therefore also asks what is visible, and an image whose visibility is `texture_or_abstract` is flagged.

`python -m scripts.try_vision hair fox`
```
--- hair ---
{
  "subject": "animal fur",
  "category": "animal",
  "visibility": "texture_or_abstract",
  "attributes": [
    "brown",
    "black",
    "coarse",
    "textured",
    "natural"
  ],
  "caption": "A close-up view of dark brown and black animal fur showing the texture of the hair.",
  "confidence": 0.95
}
status: low_confidence | tokens in/out: 1357 102
```
The fox portrait is not flagged (`status: valid`, shown in the previous section). In the batch job, three images ended up flagged (`feathers.jpg`, `hair.jpg`, `unknown.jpg`), shown in the job log below.

`docker exec -it image_matching_db psql -U capstone -d image_matching -c "SELECT i.filename, i.status, m.visibility, m.validation_status FROM images i JOIN image_metadata m ON m.image_id = i.id WHERE i.status = 'flagged';"`
```
   filename   | status  |     visibility      | validation_status 
--------------+---------+---------------------+-------------------
 feathers.jpg | flagged | texture_or_abstract | low_confidence
 hair.jpg     | flagged | texture_or_abstract | low_confidence
 unknown.jpg  | flagged | texture_or_abstract | low_confidence
(3 rows)
```

### Batch job with retries
Job 1 (12 October), the first full run over the 52 images (`python -m scripts.run_tagging_job`). The three unclear images were flagged and the job finished with no failures (excerpt of the log):
```
2026-10-12 14:46:51,087 INFO [22/52] feathers.jpg -> flagged
2026-10-12 14:47:28,163 INFO [29/52] hair.jpg -> flagged
2026-10-12 14:49:16,176 INFO [49/52] unknown.jpg -> flagged
2026-10-12 14:49:27,227 INFO Job 1 completed: 49 tagged, 3 flagged, 0 failed
 
Job 1: completed | tagged=49 flagged=3 failed=0
```

Real temporary errors from Gemini were retried with exponential backoff and jitter, and each image then succeeded (excerpt of job 2, 13 October):
```
2026-10-13 10:40:29,468 INFO HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 503 Service Unavailable"
2026-10-13 10:40:29,489 WARNING bicycle.jpg: attempt 1 failed, retrying in 3.1s
2026-10-13 10:40:41,627 INFO [4/52] bicycle.jpg -> tagged
2026-10-13 10:41:38,526 INFO HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 503 Service Unavailable"
2026-10-13 10:41:38,551 WARNING chair.jpg: attempt 1 failed, retrying in 3.9s
2026-10-13 10:41:49,202 INFO [13/52] chair.jpg -> tagged
2026-10-13 10:42:41,023 INFO HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 503 Service Unavailable"
2026-10-13 10:42:41,045 WARNING feathers.jpg: attempt 1 failed, retrying in 3.3s
2026-10-13 10:42:49,833 INFO [22/52] feathers.jpg -> flagged
2026-10-13 10:43:08,745 INFO HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 503 Service Unavailable"
2026-10-13 10:43:08,768 WARNING fox.jpg: attempt 1 failed, retrying in 3.0s
2026-10-13 10:43:16,491 INFO [25/52] fox.jpg -> tagged
```

The backoff logic is also unit-tested without a network (`python -m pytest tests/test_retry.py -v`):
```
tests/test_retry.py::test_retries_temporary_errors_then_succeeds PASSED                                                   [ 68%]
tests/test_retry.py::test_permanent_errors_are_not_retried PASSED                                                         [ 70%]
tests/test_retry.py::test_gives_up_after_max_attempts PASSED                                                              [ 71%]
```

Final state of the runs (`GET /jobs`), including the real crash (job 2) and a later no-op run (job 4):
```
id status    total processed failed
-- ------    ----- --------- ------
 4 completed     0         0      0
 3 completed     4         4      0
 2 stopped      52        48      0
 1 completed    52        52      0
```
 
The same data from the database (`SELECT id, status, total, succeeded, flagged, failed, stop_reason FROM jobs ORDER BY id;`):
```
 id |  status   | total | succeeded | flagged | failed |                      stop_reason                            ----+-----------+-------+-----------+---------+--------+-----------------------------------------------------------------
  1 | completed |    52 |        49 |       3 |      0 | 
  2 | stopped   |    52 |        46 |       2 |      0 | Interrupted: the process crashed or 
lost its database connection
  3 | completed |     4 |         3 |       1 |      0 | 
(3 rows)
```

**Crash recovery.** During job 2 the database stopped answering (`psycopg.errors.ConnectionTimeout: connection timeout expired`) after 48 of 52 images. The job was left `running` and one image was left `processing`. On the next start, recovery reset the image and closed the job with the reason shown above, and job 3 finished the remaining four images.
 
**Circuit breaker and failure alert.** With a deliberately broken embedding model name, every image failed. After three failures in a row the job stopped itself, and an `ALERT` line was logged:
```
recovered: {'images_reset': 0, 'jobs_closed': 0}
ingest: {'created': 0, 'skipped': 52}
job 5 created: 52 images to process
2026-10-15 13:16:52,766 INFO [1/52] arctic_fox.jpg -> failed
2026-10-15 13:16:54,128 INFO [2/52] baby_bear.jpg -> failed
2026-10-15 13:16:55,529 INFO [3/52] beaver.jpg -> failed
2026-10-15 13:16:56,633 INFO job 5 stopped: 0 done, 0 flagged, 3 failed (3 images failed in a row; stopping to avoid wasting calls)
2026-10-15 13:16:56,673 ERROR ALERT job 5 stopped: 0 done, 0 flagged, 3 failed (3 images failed in a row; stopping to avoid wasting calls)
 
Job 5: stopped | tagged=0 flagged=0 failed=3
Stopped because: 3 images failed in a row; stopping to avoid wasting ca
```

Starting a job over HTTP returns immediately (the work runs in the background), and when nothing is left to do it completes with zero items:
`curl.exe -i -X POST http://localhost:8000/jobs/process-images`
```
HTTP/1.1 202 Accepted
date: Wed, 21 Oct 2026 11:22:23 GMT
server: uvicorn
content-length: 211
content-type: application/json

{"id":12,"type":"process_images","status":"queued","total":0,"succeeded":0,"flagged":0,"failed":0,"stop_reason":null,"created_at":"2026-10-21T11:22:24.594685Z","started_at":null,"finished_at":null,"processed":0}
```

### Vision and embedding costs tracked per call
`curl http://localhost:8000/usage/summary`
```
{
    "by_operation":  [
                         {
                             "operation":  "embedding",
                             "calls":  73,
                             "tokens":  3899,
                             "cost":  0.0
                         },
                         {
                             "operation":  "text",
                             "calls":  21,
                             "tokens":  4861,
                             "cost":  0.0
                         },
                         {
                             "operation":  "vision",
                             "calls":  104,
                             "tokens":  157425,
                             "cost":  0.0
                         }
],
  "total_calls":  198,
  "total_cost":  0.0,
  "spent_today":  0.0,
  "daily_budget_usd":  1.0
}
```

Cost is zero because the free tier is billed at $0 and the price settings are 0. The mechanism is the same when prices are set (see the budget guard below). `GET /usage?operation=vision&limit=3` lists individual calls, each attributed to a model and an item:
`curl "http://localhost:8000/usage?operation=vision&limit=3"`
```
 id operation model                 item_id tokens_or_units cost created_at
 -- --------- -----                 ------- --------------- ---- ----------
135 vision    gemini-3.1-flash-lite      52            1584  0.0 2026-10-13T09:06:06.892889Z
134 vision    gemini-3.1-flash-lite      51            1576  0.0 2026-10-13T09:06:00.029128Z
133 vision    gemini-3.1-flash-lite      50            1589  0.0 2026-10-13T09:05:53.816860Z
```

The vision count (104) is higher than the corpus size because the corpus was tagged twice: once before and once after aliases were added.
 
**Budget guard stops the batch job.** Run with `EMBEDDING_PRICE_PER_M=20` and `DAILY_BUDGET_USD=0.002` after deleting a few image vectors:
```
2026-10-19 10:54:01,072 INFO [1/6] arctic_fox.jpg -> tagged
2026-10-19 10:54:02,693 INFO [2/6] baby_bear.jpg -> tagged
2026-10-19 10:54:04,772 INFO job 8 stopped: 2 done, 0 flagged, 0 failed (Daily 
budget of $0.002 reached)
2026-10-19 10:54:04,772 ERROR ALERT job 8 stopped: 2 done, 0 flagged, 0 
failed (Daily budget of $0.002 reached)
```

**Budget guard on `POST /posts`** (a request refused before any AI call is made). Run with `DAILY_BUDGET_USD=0.0000001` and `VISION_INPUT_PRICE_PER_M=1`, restart the API, then:
`curl.exe -i -X POST http://localhost:8000/posts -H "Content-Type: application/json" --data-binary "@post.json"`
```

PASTE HERE (expect HTTP/1.1 429 and a message about the daily AI budget)
```

and the automated version (`python -m pytest tests/test_api_budget.py -v`):
```
tests/test_api_budget.py::test_post_creation_is_refused_when_over_budget PASSED                                           [100%]
```

## Matching system

### Embeddings stored; posts return ranked suggestions
`docker exec -it image_matching_db psql -U capstone -d image_matching -c "SELECT (SELECT count(*) FROM image_vectors) AS image_vectors, (SELECT count(*) FROM post_vectors) AS post_vectors;"`
```
image_vectors | post_vectors 
---------------+--------------
            52 |           21
(1 row)
```

Probe 2: `GET /posts/1/images` for the post "How red foxes survive the winter". The fox is ranked first and suggested; the arctic fox and the red squirrel are close behind and rejected; the wolf and the wild dog are ranked last. Excerpt (ranks 4 to 8 omitted for length; run the command to see them):
`curl http://localhost:8000/posts/1/images` 
```
{
    "post_id":  1,
    "decision":  "suggested",
    "explanation":  "Subject, category, confidence and similarity all meet the required thresholds.",
    "suggested_image":  {
                            "rank":  1,
                            "image_id":  25,
                            "filename":  "fox.jpg",
                            "subject":  "red fox",
                            "category":  "animal",
                            "similarity":  0.6728,
                            "guard_passed":  true,
                            "is_suggestion":  true,
                            "reasons":  [

                                        ]
                        },
    "thresholds":  {
                       "min_similarity":  0.55,
                       "min_confidence":  0.6
                   },
    "candidates":  [
                       {
                           "rank":  1,
                           "image_id":  25,
                           "filename":  "fox.jpg",
                           "subject":  "red fox",
                           "category":  "animal",
                           "similarity":  0.6728,
                           "guard_passed":  true,
                           "is_suggestion":  true,
                           "reasons":  [

                                       ]
                       },
                       {
                           "rank":  2,
                           "image_id":  1,
                           "filename":  "arctic_fox.jpg",
                           "subject":  "arctic fox",
                           "category":  "animal",
                           "similarity":  0.6648,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected arctic fox"
                                           }
                                       ]
                       },
                       {
                           "rank":  3,
                           "image_id":  46,
                           "filename":  "squirrel.jpg",
                           "subject":  "red squirrel",
                           "category":  "animal",
                           "similarity":  0.6493,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected red squirrel"
                                           }
                                       ]
                       },
                       {
                           "rank":  4,
                           "image_id":  18,
                           "filename":  "deer.jpg",
                           "subject":  "red deer",
                           "category":  "animal",
                           "similarity":  0.5891,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected red deer"
                                           }
                                       ]
                       },
                       {
                           "rank":  5,
                           "image_id":  41,
                           "filename":  "raccoon.jpg",
                           "subject":  "raccoon",
                           "category":  "animal",
                           "similarity":  0.5857,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected raccoon"
                                           }
                                       ]
                       },
                       {
                           "rank":  6,
                           "image_id":  3,
                           "filename":  "beaver.jpg",
                           "subject":  "beaver",
                           "category":  "animal",
                           "similarity":  0.5653,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected beaver"
                                           }
                                       ]
                       },
                       {
                           "rank":  7,
                           "image_id":  40,
                           "filename":  "rabbit.jpg",
                           "subject":  "marsh rabbit",
                           "category":  "animal",
                           "similarity":  0.5639,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected marsh rabbit"
                                           }
                                       ]
                       },
                       {
                           "rank":  8,
                           "image_id":  19,
                           "filename":  "deers.jpg",
                           "subject":  "red deer",
                           "category":  "animal",
                           "similarity":  0.5632,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected red deer"
                                           }
                                       ]
                       },
                       {
                           "rank":  9,
                           "image_id":  50,
                           "filename":  "wild_dog.jpg",
                           "subject":  "african wild dog",
                           "category":  "animal",
                           "similarity":  0.5614,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected african wild dog"
                                           }
                                       ]
                       },
                       {
                           "rank":  10,
                           "image_id":  51,
                           "filename":  "wolf.jpg",
                           "subject":  "gray wolf",
                           "category":  "animal",
                           "similarity":  0.5492,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected red fox, detected gray wolf"
                                           },
                                           {
                                               "code":  "low_similarity",
                                               "message":  "Similarity 0.549 is below the threshold 0.550"
                                           }
                                       ]
                       }
                   ]
}
```

Automated version (excerpt of `python -m pytest -v`):
```
tests/test_api.py::test_fox_post_ranks_the_fox_first PASSED                                                               [ 20%]
```
 
Subject-matching rules are unit-tested (`fox` matches `arctic fox`, `red fox` does not; an alias lets a bear post find `black bear cub`):

```
tests/test_guard.py::test_normalize_name_handles_case_plurals_and_hyphens PASSED                                          [ 33%]
tests/test_guard.py::test_specific_post_rejects_a_different_species PASSED                                                [ 35%]
tests/test_guard.py::test_generic_post_matches_any_kind_of_that_animal PASSED                                              [ 36%]
tests/test_guard.py::test_alias_lets_a_general_post_find_a_specific_image PASSED                                           [ 38%]
tests/test_guard.py::test_phrase_can_appear_inside_a_longer_name PASSED                                                    [ 60%]
```

### "red fox" matches "Vulpes vulpes"
The post uses only the Latin name; the system reads it as "red fox" and
suggests the fox photo:

`$id = (Invoke-RestMethod http://localhost:8000/posts | ForEach-Object { $_ } | Where-Object { $_.title -like "Vulpes*" }).id`
`Invoke-RestMethod "http://localhost:8000/posts/$id" | Select-Object id, title, content, expected_subject, expected_category | Format-List`
```
id                : 6
title             : Vulpes vulpes: the world's most widespread wild canid
content           : Vulpes vulpes is found across the northern hemisphere, from Arctic tundra to city suburbs. Its adaptability 
                    lets it thrive on a varied diet of rodents, fruit and scraps.
expected_subject  : red fox
expected_category : animal
```

`$r = Invoke-RestMethod "http://localhost:8000/posts/$id/images"`
`$r.suggested_image | Select-Object rank, filename, subject, similarity`

```
rank filename subject similarity
---- -------- ------- ----------
   2 fox.jpg  red fox     0.6279
```

`$r.candidates | Select-Object rank, filename, similarity, guard_passed | Format-Table`
```
rank filename       similarity guard_passed
---- --------       ---------- ------------
   1 arctic_fox.jpg     0.6338        False
   2 fox.jpg            0.6279         True
   3 squirrel.jpg       0.5682        False
   4 raccoon.jpg         0.557        False
   5 wild_dog.jpg       0.5493        False
   6 dog.jpg            0.5408        False
   7 wolf.jpg           0.5392        False
   8 rabbit.jpg         0.5306        False
   9 seal.jpg           0.5221        False
  10 beaver.jpg         0.5128        False
```
In the evaluation run this post is the one the unguarded baseline gets wrong (`baseline wrong on: ['fox_latin']`) and the guarded system gets right (see Evaluation below).

## Safety layer

### The wolf-on-a-fox-post scenario provably fails
Probe 3: force the wolf as a candidate for the fox post.
`Invoke-RestMethod -Method Post -Uri http://localhost:8000/posts/1/check -ContentType "application/json" -Body '{"filename":"wolf.jpg"}' | ConvertTo-Json -Depth 6`
```
{
    "post_id":  1,
    "image_id":  51,
    "filename":  "wolf.jpg",
    "subject":  "gray wolf",
    "similarity":  0.5492,
    "decision":  "rejected",
    "explanation":  "Animal category mismatch: expected red fox, detected gray wolf; Similarity 0.549 is below the threshold 0.550",
    "reasons":  [
                    {
                        "code":  "subject_mismatch",
                        "message":  "Animal category mismatch: expected red fox, detected gray wolf"
                    },
                    {
                        "code":  "low_similarity",
                        "message":  "Similarity 0.549 is below the threshold 0.550"
                    }
                ],
    "thresholds":  {
                       "min_similarity":  0.55,
                       "min_confidence":  0.6
                   }
}
```
The wolf fails on the subject for a second reason too (low similarity). To show the subject check on its own, the dangerous candidates are the ones that clear the similarity threshold but are still wrong: the arctic fox (0.665) and the red squirrel (0.649) in the ranking above are both rejected with `subject_mismatch` alone. That is also an automated test:
`python -m pytest tests/test_guard.py::test_wolf_rejected_for_fox_post_even_with_high_similarity tests/test_api.py::test_wolf_is_rejected_for_the_fox_post tests/test_api.py::test_without_the_fox_nothing_else_passes_the_guard -v`
```
tests/test_guard.py::test_wolf_rejected_for_fox_post_even_with_high_similarity PASSED                                     [ 33%]
tests/test_api.py::test_wolf_is_rejected_for_the_fox_post PASSED                                                          [ 66%]
tests/test_api.py::test_without_the_fox_nothing_else_passes_the_guard PASSED                                              [100%]
```

Excerpt from the full run (the wolf test with a similarity of 0.88, far above the threshold, is still rejected):
```
tests/test_guard.py::test_wolf_rejected_for_fox_post_even_with_high_similarity PASSED                                     [ 41%]
tests/test_api.py::test_wolf_is_rejected_for_the_fox_post PASSED                                                          [ 18%]
```

### Rejections include a human-readable explanation
Every rejected candidate above carries a `message`, and every stored suggestion keeps the reason text. Inspect them with `GET /suggestions?post_id=1`:
 
```
id            : 1
filename      : fox.jpg
decision      : suggested
review_status : approved
reason        : Subject, category, confidence and similarity all meet the required thresholds.
 
id            : 2
filename      : arctic_fox.jpg
decision      : rejected
review_status : pending
reason        : Animal category mismatch: expected red fox, detected arctic fox
 
id            : 3
filename      : squirrel.jpg
decision      : rejected
review_status : pending
reason        : Animal category mismatch: expected red fox, detected red squirrel
```
 
(Excerpt; the full list has one entry for each of the ten candidates.)

### No confident match, with reasons
Probe 4: `GET /posts/4/images` for the post "Penguin colonies in Antarctica", which has no suitable image in the library. The system reads the subject as `emperor penguin`. Excerpt (ranks 4 to 10 omitted for length):
 
```
{
    "post_id":  4,
    "decision":  "no_confident_match",
    "explanation":  "No confident match. The top-ranked image (id 24) was rejected: Animal category mismatch: expected emperor penguin, detected greater flamingo",
    "suggested_image":  null,
    "thresholds":  {
                       "min_similarity":  0.55,
                       "min_confidence":  0.6
                   },
    "candidates":  [
                       {
                           "rank":  1,
                           "image_id":  24,
                           "filename":  "flamingo.jpg",
                           "subject":  "greater flamingo",
                           "category":  "animal",
                           "similarity":  0.579,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected emperor penguin, detected greater flamingo"
                                           }
                                       ]
                       },
                       {
                           "rank":  2,
                           "image_id":  23,
                           "filename":  "fish.jpg",
                           "subject":  "soft coral",
                           "category":  "animal",
                           "similarity":  0.5562,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected emperor penguin, detected soft coral"
                                           }
                                       ]
                       },
                       {
                           "rank":  3,
                           "image_id":  9,
                           "filename":  "camel.jpg",
                           "subject":  "dromedary camel",
                           "category":  "animal",
                           "similarity":  0.5483,
                           "guard_passed":  false,
                           "is_suggestion":  false,
                           "reasons":  [
                                           {
                                               "code":  "subject_mismatch",
                                               "message":  "Animal category mismatch: expected emperor penguin, detected dromedary camel"
                                           },
                                           {
                                               "code":  "low_similarity",
                                               "message":  "Similarity 0.548 is below the threshold 0.550"
                                           }
                                       ]
                       },
                       ... ranks 4 to 10 omitted ...
                   ]
}
```
 
Note that the top candidate's similarity (0.579) is above the threshold, so a similarity threshold alone would have accepted it. Only the subject check refused it.

## Backend

### Database models with the required indexes
Migrations (`alembic history`)
```
ee1ec310ea1c -> 0d1b5cb00fa1 (head), add aliases to image_metadata
4e7a2b391dae -> ee1ec310ea1c, add jobs table and image attempt tracking
41446e714d47 -> 4e7a2b391dae, add visibility to image_metadata
<base> -> 41446e714d47, create initial tables
```

Tables (`\dt`)
```
              List of relations
 Schema |      Name       | Type  |  Owner   
--------+-----------------+-------+----------
 public | ai_usage        | table | capstone
 public | alembic_version | table | capstone
 public | image_metadata  | table | capstone
 public | image_vectors   | table | capstone
 public | images          | table | capstone
 public | jobs            | table | capstone
 public | post_vectors    | table | capstone
 public | posts           | table | capstone
 public | reviews         | table | capstone
 public | suggestions     | table | capstone
(10 rows)
```

Vector table with the HNSW index (`\d image_vectors`)
```
                                       Table "public.image_vectors"
   Column   |           Type           | Collation | Nullable |                  Default                  
------------+--------------------------+-----------+----------+-------------------------------------------
 id         | integer                  |           | not null | nextval('image_vectors_id_seq'::regclass)
 image_id   | integer                  |           | not null | 
 embedding  | vector(768)              |           | not null | 
 model      | character varying(100)   |           | not null | 
 created_at | timestamp with time zone |           | not null | now()
Indexes:
    "image_vectors_pkey" PRIMARY KEY, btree (id)
    "ix_image_vectors_embedding_hnsw" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
    "ix_image_vectors_image_id" btree (image_id)
    "uq_image_vectors_image_model" UNIQUE CONSTRAINT, btree (image_id, model)
Foreign-key constraints:
    "image_vectors_image_id_fkey" FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
```

Images table (`\d images`)
```
                                       Table "public.images"
   Column   |           Type           | Collation | Nullable |              Default               
------------+--------------------------+-----------+----------+------------------------------------
 id         | integer                  |           | not null | nextval('images_id_seq'::regclass)
 image_url  | text                     |           | not null | 
 filename   | character varying(255)   |           | not null | 
 status     | character varying(20)    |           | not null | 
 created_at | timestamp with time zone |           | not null | now()
 attempts   | integer                  |           | not null | 0
 last_error | text                     |           |          | 
Indexes:
    "images_pkey" PRIMARY KEY, btree (id)
    "images_filename_key" UNIQUE CONSTRAINT, btree (filename)
    "ix_images_status" btree (status)
Referenced by:
    TABLE "image_metadata" CONSTRAINT "image_metadata_image_id_fkey" FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCA
DE
    TABLE "image_vectors" CONSTRAINT "image_vectors_image_id_fkey" FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    TABLE "suggestions" CONSTRAINT "suggestions_image_id_fkey" FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
```

Suggestions Table (`\d suggestions`)
```
                                          Table "public.suggestions"
      Column      |           Type           | Collation | Nullable |                 Default                 
------------------+--------------------------+-----------+----------+-----------------------------------------
 id               | integer                  |           | not null | nextval('suggestions_id_seq'::regclass)
 post_id          | integer                  |           | not null | 
 image_id         | integer                  |           | not null | 
 similarity_score | double precision         |           | not null | 
 decision         | character varying(20)    |           | not null | 
 reason           | text                     |           | not null | 
 created_at       | timestamp with time zone |           | not null | now()
Indexes:
    "suggestions_pkey" PRIMARY KEY, btree (id)
    "ix_suggestions_decision" btree (decision)
    "ix_suggestions_image_id" btree (image_id)
    "ix_suggestions_post_id" btree (post_id)
    "uq_suggestions_post_image" UNIQUE CONSTRAINT, btree (post_id, image_id)
Foreign-key constraints:
    "suggestions_image_id_fkey" FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    "suggestions_post_id_fkey" FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
Referenced by:
    TABLE "reviews" CONSTRAINT "reviews_suggestion_id_fkey" FOREIGN KEY (suggestion_id) REFERENCES suggestions(id) ON DELETE CASC
ADE
```

The first migration creates the vector extension before any table that uses it:
`Select-String -Path alembic\versions\*.py -Pattern "CREATE EXTENSION"`
```
alembic\versions\41446e714d47_create_initial_tables.py:25:    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

### API validation and the review workflow
Bad input is a clean 4xx, never a 500. Automated checks (excerpt of `python -m pytest -v`):
 
```
tests/test_api.py::test_bad_input_is_422_not_500[/images/0] PASSED                                                        [  3%]
tests/test_api.py::test_bad_input_is_422_not_500[/images/abc] PASSED                                                      [  5%]
tests/test_api.py::test_bad_input_is_422_not_500[/images?status=bogus] PASSED                                             [  6%]
tests/test_api.py::test_bad_input_is_422_not_500[/images?limit=0] PASSED                                                  [  8%]
tests/test_api.py::test_bad_input_is_422_not_500[/posts/0] PASSED                                                         [ 10%]
tests/test_api.py::test_bad_input_is_422_not_500[/posts?offset=-1] PASSED                                                 [ 11%]
tests/test_api.py::test_unknown_records_are_404 PASSED                                                                    [ 13%]
tests/test_api.py::test_post_body_is_validated_before_any_ai_call PASSED                                                  [ 15%]
tests/test_api.py::test_check_needs_exactly_one_image_identifier PASSED                                                   [ 16%]
tests/test_api_review.py::test_review_input_is_validated PASSED                                                           [ 21%]
tests/test_api_review.py::test_unknown_suggestion_is_404 PASSED                                                           [ 23%]
```

`curl -i http://localhost:8000/images/abc` (422)
```
HTTP/1.1 422 Unprocessable Content
date: Thu, 15 Oct 2026 08:50:02 GMT
```

`/images/999999` (404),
```
HTTP/1.1 404 Not Found
date: Thu, 15 Oct 2026 08:50:22 GMT
```
`Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/suggestions/1/review -ContentType "application/json" Body '{"decision":"approved","comment":"Correct fox"}'` 
```
id decision comment     created_at
-- -------- -------     ---------
1 approved Correct fox 2026-10-15T11:06:30.712419Z
```

A pairing the guard rejected cannot be approved (409).
`curl.exe -i -X POST http://127.0.0.1:8000/suggestions/2/review -H "Content-Type: application/json" -d "{\"decision\":\"approved\"}"`
```
HTTP/1.1 422 Unprocessable Content
date: Thu, 15 Oct 2026 11:10:48 GMT
```

An identical approval repeated returns the same review (201, then 200):
`'{"decision":"approved","comment":"Verified for evidence"}' | Set-Content -Encoding ascii approve2.json`
`curl.exe -i -X POST http://localhost:8000/suggestions/1/review -H "Content-Type: application/json" --data-binary "@approve2.json"` (run twice)
```
HTTP/1.1 201 Created
date: Wed, 21 Oct 2026 08:21:55 GMT
server: uvicorn
content-length: 107
content-type: application/json

{"id":2,"decision":"approved","comment":"Verified for evidence","created_at":"2026-10-21T08:21:55.589864Z"}
```

```
HTTP/1.1 200 OK
date: Wed, 21 Oct 2026 08:22:10 GMT
server: uvicorn
content-length: 107
content-type: application/json

{"id":2,"decision":"approved","comment":"Verified for evidence","created_at":"2026-10-21T08:21:55.589864Z"}
```

The same rules are tested automatically (`python -m pytest tests/test_api_review.py -v`):
```
tests/test_api_review.py::test_guard_rejected_pairing_cannot_be_approved PASSED                                           [ 25%]
tests/test_api_review.py::test_approving_the_suggestion_is_idempotent PASSED                                              [ 26%]
tests/test_api_review.py::test_cost_log_attributes_every_call PASSED                                                      [ 28%]
tests/test_api_review.py::test_job_endpoints_validate_input PASSED                                                        [ 30%]
```

## Quality and documentation

### Top-1 precision measured; number matches the README
The evaluation set is `eval/eval_set.json`: 20 posts (16 with exactly one correct image, 4 with none), each marked `dev` or `test`. The threshold was chosen on `dev` only.
 
**Threshold sweep on the dev split** (`python -m scripts.evaluate --sweep`). The left block is the guarded system, the right block is a baseline with no guard (take the most similar image and accept it if it clears the threshold). `ref` is unanswerable posts refused correctly, `FA` is false accepts: 
```
       |------- with guard -------|  |---- threshold only (no guard) ----|
thr    correct missed wrong | ref  FA   correct missed wrong | ref  FA
0.30    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.31    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.32    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.33    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.34    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.35    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.36    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.37    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.38    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.39    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.40    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.41    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.42    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.43    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.44    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.45    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.46    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.47    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.48    8/8      0     0 |  2   0     8/8      0     0 |  0   2
0.49    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.50    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.51    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.52    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.53    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.54    8/8      0     0 |  2   0     8/8      0     0 |  1   1
0.55    8/8      0     0 |  2   0     8/8      0     0 |  2   0
0.56    8/8      0     0 |  2   0     8/8      0     0 |  2   0
0.57    8/8      0     0 |  2   0     8/8      0     0 |  2   0
0.58    8/8      0     0 |  2   0     8/8      0     0 |  2   0
0.59    8/8      0     0 |  2   0     8/8      0     0 |  2   0
0.60    7/8      1     0 |  2   0     7/8      1     0 |  2   0
0.61    6/8      2     0 |  2   0     6/8      2     0 |  2   0
0.62    6/8      2     0 |  2   0     6/8      2     0 |  2   0
0.63    5/8      3     0 |  2   0     5/8      3     0 |  2   0
0.64    5/8      3     0 |  2   0     5/8      3     0 |  2   0
0.65    4/8      4     0 |  2   0     4/8      4     0 |  2   0
0.66    4/8      4     0 |  2   0     4/8      4     0 |  2   0
0.67    3/8      5     0 |  2   0     3/8      5     0 |  2   0
0.68    2/8      6     0 |  2   0     2/8      6     0 |  2   0
0.69    2/8      6     0 |  2   0     2/8      6     0 |  2   0
0.70    1/8      7     0 |  2   0     1/8      7     0 |  2   0
0.71    1/8      7     0 |  2   0     1/8      7     0 |  2   0
0.72    1/8      7     0 |  2   0     1/8      7     0 |  2   0
0.73    1/8      7     0 |  2   0     1/8      7     0 |  2   0
0.74    0/8      8     0 |  2   0     0/8      8     0 |  2   0
0.75    0/8      8     0 |  2   0     0/8      8     0 |  2   0
```

With the guard, results are perfect from 0.30 to 0.59, and the first correct match is lost at 0.60. Without it, the unanswerable posts are only refused correctly from 0.55, so the safe window shrinks to 0.55 to 0.59. `MIN_SIMILARITY = 0.55` was chosen inside the guarded plateau with a margin below the cliff.
 
**Final report** (`python -m scripts.evaluate --json eval/results.json`, at `min_similarity = 0.55`):
```
min_similarity = 0.55   min_confidence = 0.6

[dev] top-1 precision: 8/8 = 1.0  | wrong suggestions: 0  | missed (safe refusals): 0  | refused correctly: 2/2  | false accepts: 0

[test] top-1 precision: 7/8 = 0.875  | wrong suggestions: 0  | missed (safe refusals): 1  | refused correctly: 1/2  | false accepts: 1

[all] top-1 precision: 15/16 = 0.938  | wrong suggestions: 0  | missed (safe refusals): 1  | refused correctly: 3/4  | false accepts: 1
baseline wrong on: ['fox_latin']

baseline (rank-1 by similarity only, never refuses): 15/16 correct, 4/4 unanswerable posts got an image

posts that did not come out right:
  [test] house       missed       expected=house.jpg got=no confident match
      subject read as 'house': No confident match. The top-ranked image (id 33) was rejected: Similarity 0.549 is below the threshold 0.550
  [test] toad        false_accept expected=None got=frog.jpg
      subject read as 'toad': Subject, category, confidence and similarity all meet the required thresholds.
```
The number in the README is the held-out **test** split: **7/8 = 0.875**. The two misses are explained in the README's limitations: `house` scored 0.549 against a threshold of 0.55 (a safe refusal), and `toad` was matched to the frog because the vision model gave the frog image the alias `toad`.


### README and required files present
`git ls-files README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example LICENSE docs/design-one-page.md docs/design-document.md`
```
.env.example
BUILDLOG.md
EVIDENCE.md
LICENSE
README.md
capstone.yaml
```

The design documents are `docs/design-one-page.md` (the one-page Phase 1 design) and `docs/design-document.md` (the full design as built, with the changes from the original plan).

## Shared requirements

### 1. Layered architecture
`Get-ChildItem app -Recurse -Filter *.py -Name | Sort-Object`
```
__init__.py
api\__init__.py
api\deps.py
api\images.py
api\jobs.py
api\posts.py
api\reviews.py
api\suggestions.py
api\usage.py
config.py
database.py
jobs\__init__.py
jobs\generate_embeddings.py
jobs\process_images.py
jobs\tagging_job.py
logging_setup.py
main.py
models\__init__.py
models\embedding.py
models\image.py
models\job.py
models\post.py
models\review.py
models\suggestion.py
models\usage.py
schemas\__init__.py
schemas\api.py
schemas\image.py
schemas\post.py
schemas\review.py
schemas\vision.py
services\__init__.py
services\embeddings.py
services\errors.py
services\ingestion.py
services\matching.py
services\mismatch_guard.py
services\post_analysis.py
services\posts.py
services\retry.py
services\reviews.py
services\tagging.py
services\usage.py
services\vision.py
```
 
The mismatch guard is a pure module with no database or network dependency:
`Select-String -Path app\services\mismatch_guard.py -Pattern "^(import|from) "`
```
app\services\mismatch_guard.py:8:import re
app\services\mismatch_guard.py:9:from dataclasses import dataclass
app\services\mismatch_guard.py:10:from enum import Enum
app\services\mismatch_guard.py:11:from collections.abc import Iterable
```
 
### 2. Validation at the boundary
 
See "API validation and the review workflow" above: 422 for malformed requests, 404 for unknown records, 409 for state conflicts, 429 for an exhausted budget, 502 for an AI provider failure, never a 500 for bad input.
 
### 3. Background job with retries and a failure alert
 
See "Images are processed through a batch background job with retries" above: real 503 retries with backoff, crash recovery, the circuit breaker, and the logged `ALERT` line.
 
### 4. Real persistence
 
Schema is Alembic migrations (see Database above). A fresh clone reproduces the system from nothing with one command and a seed step. Transcript of the clean-clone test (a new clone in a new folder, a new Docker volume, no API key):
 
```
docker compose up --build --wait
 ✔ Image clean-test-api        Built                                                                                       107.3s
 ✔ Network clean-test_default  Created                                                                                       0.2s
 ✔ Volume clean-test_pgdata    Created                                                                                       0.0s
 ✔ Container image_matching_db Healthy                                                                                      10.2s
 ✔ Container clean-test-api-1  Healthy                                                                                      20.8s
 
docker compose exec -T api python -m scripts.seed
images registered: {'created': 52, 'skipped': 0}
tags loaded: 52 | posts created: 21 | usage rows loaded: 198
 
docker compose exec -T api python -m scripts.evaluate
 
min_similarity = 0.55   min_confidence = 0.6
 
[dev] top-1 precision: 8/8 = 1.0  | wrong suggestions: 0  | missed (safe refusals): 0  | refused correctly: 2/2  | false accepts: 0
 
[test] top-1 precision: 7/8 = 0.875  | wrong suggestions: 0  | missed (safe refusals): 1  | refused correctly: 1/2  | false accepts: 1
 
[all] top-1 precision: 15/16 = 0.938  | wrong suggestions: 0  | missed (safe refusals): 1  | refused correctly: 3/4  | false accepts: 1
baseline wrong on: ['fox_latin']
baseline (rank-1 by similarity only, never refuses): 15/16 correct, 4/4 unanswerable posts got an image
 
posts that did not come out right:
  [test] house       missed       expected=house.jpg got=no confident match
      subject read as 'house': No confident match. The top-ranked image (id 33) was rejected: Similarity 0.549 is below the threshold 0.550
  [test] toad        false_accept expected=None got=frog.jpg
      subject read as 'toad': Subject, category, confidence and similarity all meet the required thresholds.
 
docker compose exec -T api python -m pytest -q
............................................................             [100%]
60 passed, 2 warnings in 3.33s
```
 
The evaluation result is identical to the development machine's, with no API key, because the seed loads the recorded tags and vectors.
 
### 5. Idempotency
 
| Operation | Proof |
|---|---|
| Registering images | `ingest: {'created': 0, 'skipped': 52}` on every re-run (see the job output above) |
| Batch job with nothing left to do | Job 4 ran and completed with `total 0` (see the jobs table above) |
| Review submitted twice | 201 then 200 with the same id (see the review transcript above) |
| Seed run twice | See below |
 
`docker compose exec -T api python -m scripts.seed`
`docker compose exec -T api python -m scripts.seed`
```
images registered: {'created': 0, 'skipped': 52}
tags loaded: 0 | posts created: 0 | usage rows loaded: 0
```
 
### 6. Secrets clean
 
`.env` is git-ignored and only `.env.example` (placeholders) is tracked. Neither the file nor a Google API key prefix appears anywhere in the history:
 
```
git ls-files | Select-String "\.env"
.env.example
 
git log --all --oneline -- .env
(nothing)
 
git log --all --oneline -S"AIza"
(nothing)
```
 
Exception text is never logged for the alert webhook, because it could contain the webhook URL.
 
### 7. Cost tracked, with a budget guard
 
See "Vision and embedding costs are tracked per call" above: per-call rows, the summary, and the budget guard stopping the batch job and refusing `POST /posts`.
 
---
 
## Known limitations
 
These are shown here, not hidden, because they are part of what the numbers mean. The README has the full list.
 
- The evaluation is small (8 answerable posts per split), so one post moves precision by 12.5 points.
- The vision model's confidence was 0.95 to 1.0 for almost every image, so flagging relies on the `visibility` field.
- Re-tagging the same image can change its tags, so the demo data is a recorded run.
- A wrong alias on one image (`frog` listed as `toad`) causes the one false accept in the evaluation.
 