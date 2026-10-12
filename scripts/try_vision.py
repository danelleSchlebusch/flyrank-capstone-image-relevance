import sys
from pathlib import Path

from app.services.vision import analyze_image

names = sys.argv[1:] or ["unknown","hair","feathers", "bird"]
for name in names:
    result = analyze_image(Path(f"data/images/{name}.jpg"))
    print(f"\n--- {name} ---")
    print(result.tags.model_dump_json(indent=2))
    print("status:", result.validation_status,
          "| tokens in/out:", result.input_tokens, result.output_tokens)