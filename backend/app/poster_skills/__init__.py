from pathlib import Path


def instructions(name: str) -> str:
    """Load bundled, versioned skills; never load paths supplied by a model."""
    if name not in {"recruitment-post-writer", "recruitment-content-strategist"}:
        raise ValueError("Unknown poster skill")
    text = (Path(__file__).parent / name / "SKILL.md").read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip()
