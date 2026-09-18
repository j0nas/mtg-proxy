from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgproxy.paths import SCM, TEMPLATES

FIXTURES = Path(__file__).parent / "fixtures"
STOCK_A4 = SCM / "cutting_templates" / "a4-standard-v5.studio3"
BASE_A4 = TEMPLATES / "a4-standard-v5-alpha.studio3"

needs_engine = pytest.mark.skipif(not (SCM / "create_pdf.py").is_file(), reason="vendored engine not cloned")
needs_stock_template = pytest.mark.skipif(not STOCK_A4.is_file(), reason="stock template not present")


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
