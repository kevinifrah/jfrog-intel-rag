import re
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent

_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n.*?\n---\s*\n", re.DOTALL)


def load_skill(name: str) -> str:
    """Return the body of <name>/SKILL.md with YAML frontmatter stripped."""
    path = _SKILLS_DIR / name / "SKILL.md"
    raw = path.read_text(encoding="utf-8")
    body = _FRONTMATTER_RE.sub("", raw, count=1)
    return body.strip()


def compose(*names: str) -> str:
    """Return skill bodies joined by blank lines (used to build system prompts)."""
    return "\n\n".join(load_skill(n) for n in names)
