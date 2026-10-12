"""Rename and resize the raw images into data/images/ (max 800px, JPEG)."""
import re
from pathlib import Path

from PIL import Image, ImageOps

RAW = Path("data/raw")
OUT = Path("data/images")
MAX_SIDE = 800
TYPO_FIXES = {"rabit": "rabbit"}


def clean_name(stem: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    for wrong, right in TYPO_FIXES.items():
        name = name.replace(wrong, right)
    return name


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = [p for p in RAW.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    for path in sorted(files):
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            target = OUT / f"{clean_name(path.stem)}.jpg"
            img.save(target, "JPEG", quality=85)
            print(f"{path.name} -> {target.name}")
    print(f"Done: {len(files)} images")


if __name__ == "__main__":
    main()