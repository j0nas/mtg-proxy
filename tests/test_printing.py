from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream

from mtgproxy import printing
from mtgproxy.cameo import proof, sensing, session
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
    assert "d-portrait.pdf: A4, 2 page(s), 1 turned upright; on the mat the square goes top-left" in lines


def test_a_portrait_sheet_says_nothing_about_the_mat(tmp_path):
    lines = []
    print_pdf(
        _pdf(tmp_path / "d.pdf", [(210, 297)]), "plain", dry_run=True, runner=FakePc(), say=lines.append
    )
    assert "d-portrait.pdf: A4, 1 page(s), 0 turned upright" in lines


def test_a_turned_page_prints_its_square_where_the_check_looks_on_a_sheet_loaded_as_printed(tmp_path):
    """sensing.turned assumes make_upright turns a page a quarter clockwise: render the upright page
    (pdfium applies /Rotate) and find the deck box's square on it."""
    frame = session.Frame(page_w=297, page_h=210, inset=5, width=287, height=200)
    k, side = 1 / printing.PT_MM, proof.SQUARE
    w = PdfWriter()
    page = w.add_blank_page(frame.page_w * k, frame.page_h * k)
    square = ContentStream(None, w)  # PDF y runs up from the bottom edge
    square.set_data(
        f"0 g {frame.inset * k} {(frame.page_h - frame.top - side) * k} {side * k} {side * k} re f".encode()
    )
    page.replace_contents(square)
    w.write(tmp_path / "box.pdf")
    printing.make_upright(tmp_path / "box.pdf", tmp_path / "upright.pdf")
    per_mm = 10
    doc = pdfium.PdfDocument(str(tmp_path / "upright.pdf"))
    grey = doc[0].render(scale=per_mm / k, grayscale=True).to_numpy()
    doc.close()
    grey = grey[..., 0] if grey.ndim == 3 else grey
    assert (grey.shape[1] / per_mm, grey.shape[0] / per_mm) == pytest.approx((210, 297), abs=0.2)  # portrait
    ys, xs = np.nonzero(grey < 128)
    middle = ((xs.min() + xs.max() + 1) / 2 / per_mm, (ys.min() + ys.max() + 1) / 2 / per_mm)
    t = sensing.turned(frame)
    assert middle == pytest.approx((t.x, t.y), abs=0.1)  # (202.5, 7.5): top-right


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
