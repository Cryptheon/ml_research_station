"""Central prompt loader for ResearchStation.

All prompt text lives in ``src/research_station/prompts/*.md``.
Each file has a YAML frontmatter block (between ``---`` delimiters) that
documents the prompt's purpose and variables, followed by the template body.

Templates use Python's ``string.Template`` syntax:
  - ``$variable`` or ``${variable}`` — substituted at render time
  - ``$$``                           — produces a literal ``$`` in the output
  - ``{`` / ``}``                    — passed through unchanged (safe for LaTeX)

Usage::

    from research_station.processing.prompts import render, load

    # Render in one call
    prompt = render("ocr_page", page=3, context_line="GPT-4 — OpenAI")

    # Or get the Template object for repeated use
    tmpl = load("summarizer_user")
    prompt = tmpl.substitute(title="...", ...)
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


@cache
def load(name: str) -> Template:
    """Load a prompt template by name (without ``.md`` extension).

    The result is cached so repeated calls don't re-read the file at runtime.
    Call ``load.cache_clear()`` in tests if you need to reload.

    Args:
        name: Filename stem, e.g. ``"ocr_page"`` for ``prompts/ocr_page.md``.

    Returns:
        A ``string.Template`` ready for ``.substitute(**vars)``.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Available prompts: {[p.stem for p in PROMPTS_DIR.glob('*.md')]}"
        )
    raw = path.read_text(encoding="utf-8")
    body = _strip_frontmatter(raw).strip()
    return Template(body)


def render(name: str, **variables: object) -> str:
    """Load and render a prompt template in one call.

    Args:
        name:      Prompt name (file stem, e.g. ``"chat_system"``).
        **variables: Substitution variables matching the template's ``$var`` placeholders.

    Returns:
        Rendered prompt string.

    Raises:
        KeyError:         If a required variable is missing.
        FileNotFoundError: If the prompt file does not exist.
    """
    return load(name).substitute(**variables)


def render_partial(name: str, **variables: object) -> str:
    """Like ``render`` but leaves unrecognised ``$var`` placeholders intact.

    Useful when you build the prompt in stages — e.g. render static variables
    first, then add dynamic context later.
    """
    return load(name).safe_substitute(**variables)


def list_prompts() -> list[str]:
    """Return the names of all available prompt files (without extension)."""
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


# ── Private ───────────────────────────────────────────────────────────────────


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block if present."""
    return _FRONTMATTER_RE.sub("", text, count=1)
