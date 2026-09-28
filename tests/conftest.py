from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgproxy.paths import SCM

FIXTURES = Path(__file__).parent / "fixtures"

needs_engine = pytest.mark.skipif(not (SCM / "create_pdf.py").is_file(), reason="vendored engine not cloned")


@pytest.fixture
def archidekt_json() -> dict:
    return json.loads((FIXTURES / "archidekt_deck.json").read_text())


@pytest.fixture
def moxfield_json() -> dict:
    return json.loads((FIXTURES / "moxfield_deck.json").read_text())


@pytest.fixture
def decklist(tmp_path: Path) -> Path:
    p = tmp_path / "mydeck.txt"
    p.write_text("1 Sol Ring (CMM) 464\n1 Lightning Bolt\n")
    return p
