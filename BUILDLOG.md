# Build log (AI usage)

AI assistant used: Claude, as a step-by-step tutor and code generator.
I ran and checked everything myself; below is where the AI was wrong or I changed course.

## Where the AI was wrong, and what I did
- **Retired model.** The AI's first instinct was a Gemini 2.5 model that was due to be
  retired. We searched for current names and put the model in config.
- **Garbage-collected client.** AI-generated code built a new Gemini client per call;
  it was closed mid-request. Found by running it; fixed with a cached client.
- **Confidence was useless.** Every image, even blurry texture shots, scored 0.95+. I
  tested with two extra hard images, and we added a `visibility` field.
- **Guard compared last words.** The AI's guard rejected "bear" vs "black bear cub" and
  about 1 in 5 of my images. Its unit tests passed because they were written around
  easy examples. I found it by reading my real tags; we added aliases and
  whole-phrase matching.
- **Threshold placeholder.** The AI's 0.70 was a guess and rejected everything. I
  chose 0.55 from a labelled dev/test sweep.
- **Batch job crash gap.** A real database timeout left an image stuck in
  "processing" and a job "running" forever; we added recovery and an alert.

## Where I disagreed, corrected or checked
- I misidentified `hawk.jpg`; the model was right (golden eagle).
- My own design doc said the wolf should rank high on similarity. It ranked last. Checking the claim against real data led to rewriting the probe.

## Lines I can explain
- `subject_matches` in `mismatch_guard.py`
- `call_with_backoff` in `retry.py`
- `_needs_work` in `tagging_job.py`