from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from mtgproxy import printing
from mtgproxy.printing import PrintError, print_pdf


def _pdf(path: Path, sizes_mm: list[tuple[float, float]]) -> Path:
    w = PdfWriter()
    for mw, mh in sizes_mm:
        w.add_blank_page(mw / printing.PT_MM, mh / printing.PT_MM)
    w.write(path)
    return path


class FakePc:
    """Stands in for ssh/scp: records argv, answers the queue and size checks."""

    def __init__(self, queues=("ET-8550 Series(Network)", "Glossy photo paper proxies")):
        self.queues, self.calls, self.scripts, self.copied = set(queues), [], [], []

    def __call__(self, argv, stdin=None):
        self.calls.append(argv)
        out = ""
        if argv[0] == "scp":
            src = Path(argv[-2])
            self.copied.append((src.name, argv[-1], src.stat().st_size, PdfReader(src)))
            self.size = src.stat().st_size
        else:
            script = base64.b64decode(argv[-1].split()[-1]).decode("utf-16-le")
            self.scripts.append(script)
            if "Get-Printer" in script:
                out = "present" if any(f"'{q}'" in script for q in self.queues) else "missing"
            elif ".Length" in script:
                out = str(self.size)
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")


def test_landscape_rotated_portrait_untouched(tmp_path):
    pdf = _pdf(tmp_path / "d.pdf", [(297, 210), (210, 297)])
    pc, lines = FakePc(), []
    print_pdf(pdf, "plain", dry_run=True, runner=pc, say=lines.append)
    pages = pc.copied[0][3].pages
    assert [p.get("/Rotate") or 0 for p in pages] == [90, 0]
    assert any("1 turned upright" in line and "2 page(s)" in line for line in lines)


def test_dry_run_copies_but_never_prints(tmp_path):
    pc, lines = FakePc(), []
    r = print_pdf(_pdf(tmp_path / "d.pdf", [(210, 297)]), "glossy", dry_run=True, runner=pc, say=lines.append)
    assert not r.sent
    assert not any("SumatraPDF" in s for s in pc.scripts)
    assert pc.copied[0][0] == "d-portrait.pdf"
    assert pc.copied[0][1].endswith(f"{printing.REMOTE_DIR}/d-portrait.pdf")
    assert "dry run" in lines[-1] and "SumatraPDF" in lines[-1]


@pytest.mark.parametrize(
    ("kind", "queue"),
    [
        ("plain", "ET-8550 Series(Network)"),
        ("glossy", "Glossy photo paper proxies"),
        ("premium", "Premium glossy paper"),
    ],
)
def test_queue_mapping_and_print_command(tmp_path, kind, queue):
    pc = FakePc(queues=[queue])
    pdf = _pdf(tmp_path / "d.pdf", [(210, 297)])
    r = print_pdf(pdf, kind, runner=pc, say=lambda s: None)
    assert r.sent and r.queue == queue
    last = pc.scripts[-1]
    assert f"-print-to '{queue}'" in last
    assert "-print-settings 'noscale,portrait,paper=A4'" in last
    assert "-silent -exit-when-done" in last and "| Out-Null" in last
    assert f"Get-PrintJob -PrinterName '{queue}'" in last
    assert sum("SumatraPDF" in s for s in pc.scripts) == 1  # sent exactly once


def test_scp_argv(tmp_path):
    pc = FakePc()
    print_pdf(_pdf(tmp_path / "d.pdf", [(210, 297)]), "plain", dry_run=True, runner=pc, say=lambda s: None)
    scp = next(c for c in pc.calls if c[0] == "scp")
    assert scp[:4] == ["scp", "-q", "-i", str(printing.KEY)]
    assert scp[-1] == f"jonas@pomelomadness.local:{printing.REMOTE_DIR}/d-portrait.pdf"


@pytest.mark.parametrize(
    ("size", "paper"), [((297, 420), "A3"), ((215.9, 279.4), "letter"), ((420, 297), "A3")]
)
def test_paper_from_page_size(tmp_path, size, paper):
    _, _, _ = printing.make_upright(_pdf(tmp_path / "a.pdf", [size]), tmp_path / "b.pdf")
    assert printing.make_upright(tmp_path / "a.pdf", tmp_path / "b.pdf")[0] == paper


def test_odd_size_and_mixed_sizes_rejected(tmp_path):
    with pytest.raises(PrintError, match="not A4"):
        printing.make_upright(_pdf(tmp_path / "a.pdf", [(100, 150)]), tmp_path / "b.pdf")
    with pytest.raises(PrintError, match="mix"):
        printing.make_upright(_pdf(tmp_path / "a.pdf", [(210, 297), (297, 420)]), tmp_path / "b.pdf")


def test_missing_queue_fails_before_copy(tmp_path):
    pc = FakePc(queues=[])
    with pytest.raises(PrintError, match="Premium glossy paper"):
        print_pdf(_pdf(tmp_path / "d.pdf", [(210, 297)]), "premium", runner=pc, say=lambda s: None)
    assert not pc.copied and len(pc.calls) == 1


def test_unknown_paper_and_unreachable_pc(tmp_path):
    pdf = _pdf(tmp_path / "d.pdf", [(210, 297)])
    with pytest.raises(PrintError, match="unknown paper"):
        print_pdf(pdf, "matte", runner=FakePc(), say=lambda s: None)
    down = lambda argv, stdin=None: subprocess.CompletedProcess(argv, 255, "", "")  # noqa: E731
    with pytest.raises(PrintError, match="cannot reach"):
        print_pdf(pdf, "plain", runner=down, say=lambda s: None)
