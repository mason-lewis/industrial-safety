"""Retire library entries while keeping their stable paths for saved evidence."""
import json
from pathlib import Path


def hidden_sources(root):
    path = Path(root) / 'data/library-hidden.json'
    if not path.exists():
        return set()
    return set(json.loads(path.read_text())['source_ids'])
