# Build Log

---

## Mismatch Guard Structure

I made use of Claude to build the guard structure for this project.

I ran the following code to test the different guards set in place.

```bash
python -m pytest -v
```

The results showed that all the tests passed, especially the **test_wolf_rejected_for_fox_post_even_with_high_similarity** test which showed that even if a wolf has a similarity of 0.88, which is well above the threshold, it will still be rejected.