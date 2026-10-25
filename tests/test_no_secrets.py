"""Guard against committing real keys (an OpenRouter key and a GitHub token were once committed and revoked)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Real-key shapes: long enough that placeholders like "sk-or-v1-abc..." in test mocks don't match.
KEY_PATTERNS = [
    re.compile(r"sk-or-v1-[0-9a-f]{40,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
]


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.splitlines()]


def test_no_real_keys_in_tracked_files():
    hits = []
    for path in tracked_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in KEY_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert not hits, f"key-shaped strings in tracked files: {hits}"


def test_env_example_has_blank_keys():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith(("OPENROUTER_API_KEY=", "GITHUB_TOKEN=", "SEMANTIC_SCHOLAR_API_KEY=")):
            assert line.split("=", 1)[1].strip() == "", f".env.example must stay blank: {line.split('=')[0]}"
