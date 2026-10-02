#!/usr/bin/env python3
"""Extract the HesMartech Client Project Intake Form into structured data.

The source PDF is a filled AcroForm.  Every question label lives in the page
content stream, and every answer lives in the form-field tree, so the two have
to be joined geometrically: a widget's rectangle is matched against the text
runs printed around it.

Zero dependencies -- the PDF is parsed directly (classic xref, Flate streams).

    python3 tools/extract_intake.py mog.pdf --json docs/intake.json
    python3 tools/extract_intake.py mog.pdf --markdown docs/REQUIREMENTS.md
"""
from __future__ import annotations

import argparse
import binascii
import json
import re
import sys
import zlib
from dataclasses import dataclass, field as dc_field
from pathlib import Path

# --------------------------------------------------------------------------
# Minimal PDF object layer
# --------------------------------------------------------------------------


class Pdf:
    """Just enough of a PDF reader for a linear, uncompressed-xref document."""

    def __init__(self, data: bytes):
        self.data = data
        self.objects: dict[int, bytes] = {}
        for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b", data):
            num = int(m.group(1))
            end = data.find(b"endobj", m.end())
            if end == -1:
                continue
            self.objects[num] = data[m.end():end]

    def obj(self, num: int) -> bytes:
        return self.objects[num]

    def stream(self, num: int) -> bytes:
        body = self.objects[num]
        m = re.search(rb"stream\r?\n", body)
        if not m:
            return b""
        raw = body[m.end():body.rfind(b"endstream")]
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw

    @staticmethod
    def refs(blob: bytes) -> list[int]:
        return [int(x) for x in re.findall(rb"(\d+)\s+0\s+R", blob)]

    @staticmethod
    def text(token: bytes) -> str:
        """Decode a PDF string literal or hex string, honouring UTF-16 BOMs."""
        if token.startswith(b"<"):
            raw = binascii.unhexlify(re.sub(rb"[^0-9A-Fa-f]", b"", token))
            if raw[:2] == b"\xfe\xff":
                return raw[2:].decode("utf-16-be")
            return raw.decode("latin-1")
        body = token[1:-1]
        out = bytearray()
        escapes = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == 0x5C and i + 1 < len(body):
                i += 1
                out.append(escapes.get(body[i], body[i]))
            else:
                out.append(ch)
            i += 1
        return out.decode("latin-1")


STRING_TOKEN = rb"<[0-9A-Fa-f\s]*>|\((?:[^()\\]|\\.)*\)"


@dataclass
class TextRun:
    x: float
    y: float
    text: str
    font: str
    size: float

    @property
    def bold(self) -> bool:
        return "Bold" in self.font


@dataclass
class Field:
    name: str
    kind: str          # text | textarea | radio | checkbox
    page: int
    rect: list[float]
    value: str = ""
    options: list[str] = dc_field(default_factory=list)
    label: str = ""

    @property
    def section(self) -> str:
        return self.name.split("_")[0]

    @property
    def answered(self) -> bool:
        return bool(self.value)


SECTIONS = {
    "header": "Header",
    "s1": "Website Management, Hosting & Domain",
    "s2": "About You & Your Business",
    "s3": "Project Type",
    "s4": "Goals & Purpose",
    "s5": "Target Audience & Users",
    "s6": "Features & Functionality",
    "s7": "Design & Appearance",
    "s8": "Content & Pages",
    "s9": "Technical Requirements",
    "s10": "Timeline & Budget",
    "s11": "Priority & Anything Else",
    "sig": "Declaration",
}


class IntakeForm:
    def __init__(self, path: Path):
        self.pdf = Pdf(path.read_bytes())
        self.pages = self._page_refs()
        self.runs = {n: self._text_runs(ref) for n, ref in enumerate(self.pages, 1)}
        self._widget_page: dict[int, int] = {}
        self._widget_rect: dict[int, list[float]] = {}
        self._index_widgets()
        self.fields = self._read_fields()
        self._attach_labels()

    # -- structure ---------------------------------------------------------

    def _page_refs(self) -> list[int]:
        catalog = next(
            body for body in self.pdf.objects.values()
            if b"/Type/Catalog" in body.replace(b" ", b"")
        )
        pages_ref = Pdf.refs(re.search(rb"/Pages\s+(\d+\s+0\s+R)", catalog).group(1))[0]
        kids = re.search(rb"/Kids\[([^\]]*)\]", self.pdf.obj(pages_ref)).group(1)
        return Pdf.refs(kids)

    def _text_runs(self, page_ref: int) -> list[TextRun]:
        contents = Pdf.refs(
            re.search(rb"/Contents\s*\[?\s*([\d\s]+R)", self.pdf.obj(page_ref)).group(1)
        )
        buf = b"".join(self.pdf.stream(c) for c in contents)
        runs: list[TextRun] = []
        pos = (0.0, 0.0)
        font, size = "", 0.0
        pattern = (
            rb"1 0 0 1 ([\d.\-]+) ([\d.\-]+) Tm"
            rb"|<([0-9A-Fa-f]*)>\s*Tj"
            rb"|/([A-Za-z\-]+)-\d+\s+([\d.]+) Tf"
        )
        for m in re.finditer(pattern, buf):
            if m.group(1):
                pos = (float(m.group(1)), float(m.group(2)))
            elif m.group(3) is not None:
                decoded = binascii.unhexlify(m.group(3)).decode("latin-1")
                runs.append(TextRun(pos[0], pos[1], decoded, font, size))
            else:
                font, size = m.group(4).decode(), float(m.group(5))
        return runs

    def _index_widgets(self) -> None:
        for page_no, page_ref in enumerate(self.pages, 1):
            annots = re.search(rb"/Annots\[([^\]]*)\]", self.pdf.obj(page_ref))
            if not annots:
                continue
            for ref in Pdf.refs(annots.group(1)):
                rect = re.search(rb"/Rect\s*\[([^\]]*)\]", self.pdf.obj(ref))
                if rect:
                    self._widget_rect[ref] = [float(v) for v in rect.group(1).split()]
                    self._widget_page[ref] = page_no

    # -- fields ------------------------------------------------------------

    def _read_fields(self) -> list[Field]:
        acro_ref = Pdf.refs(
            re.search(
                rb"/AcroForm\s+(\d+\s+0\s+R)",
                next(b for b in self.pdf.objects.values() if b"/AcroForm" in b),
            ).group(1)
        )[0]
        listing = re.search(rb"/Fields\s*\[(.*?)\]", self.pdf.obj(acro_ref), re.S).group(1)

        fields: list[Field] = []
        for ref in Pdf.refs(listing):
            body = self.pdf.obj(ref)
            name = Pdf.text(re.search(rb"/T\s*(" + STRING_TOKEN + rb")", body).group(1))
            ftype = re.search(rb"/FT\s*/(\w+)", body).group(1).decode()
            flags = int(m.group(1)) if (m := re.search(rb"/Ff\s+(\d+)", body)) else 0

            kind = {"Tx": "text", "Btn": "checkbox", "Ch": "choice"}[ftype]
            if ftype == "Btn" and flags & 0x8000:      # Radio
                kind = "radio"
            elif ftype == "Tx" and flags & 0x1000:     # Multiline
                kind = "textarea"

            value = ""
            vm = re.search(rb"/V\s*(" + STRING_TOKEN + rb"|/[\w.]+)", body, re.S)
            if vm:
                token = vm.group(1)
                value = Pdf.text(token) if token[:1] in (b"<", b"(") else token[1:].decode()

            options: list[str] = []
            om = re.search(rb"/Opt\s*\[(.*?)\]", body, re.S)
            if om:
                options = [Pdf.text(t) for t in re.findall(STRING_TOKEN, om.group(1))]

            kids = Pdf.refs(re.search(rb"/Kids\s*\[([^\]]*)\]", body).group(1))
            first = kids[0]
            fields.append(Field(
                name=name, kind=kind, page=self._widget_page[first],
                rect=self._widget_rect[first], value=value, options=options,
            ))
        return fields

    def _attach_labels(self) -> None:
        for f in self.fields:
            if f.kind == "checkbox":
                f.label = self._run_right_of(f) or ""
            else:
                f.label = self._bold_above(f) or ""

    def _run_right_of(self, f: Field, tol: float = 4.0) -> str | None:
        baseline = f.rect[1] + 3.6
        right = [
            r for r in self.runs[f.page]
            if abs(r.y - baseline) < tol and r.x > f.rect[2]
        ]
        right.sort(key=lambda r: r.x)
        return right[0].text if right else None

    def _bold_above(self, f: Field, max_gap: float = 40.0) -> str | None:
        best: tuple[tuple[float, float], str] | None = None
        for r in self.runs[f.page]:
            if not r.bold or r.size < 9.5:
                continue
            gap = r.y - f.rect[3]
            if 0 < gap < max_gap and r.x <= f.rect[0] + 6:
                key = (gap, abs(r.x - f.rect[0]))
                if best is None or key < best[0]:
                    best = (key, r.text)
        return best[1] if best else None

    # -- output ------------------------------------------------------------

    def grouped(self) -> list[tuple[str, list[Field]]]:
        order, buckets = [], {}
        for f in self.fields:
            if f.section not in buckets:
                buckets[f.section] = []
                order.append(f.section)
            buckets[f.section].append(f)
        return [(s, buckets[s]) for s in order]

    def to_dict(self) -> dict:
        out: dict = {"form": "HesMartech Client Project Intake Form", "sections": []}
        for key, fields in self.grouped():
            entries = []
            for f in fields:
                entry = {"id": f.name, "kind": f.kind, "page": f.page, "label": f.label}
                if f.kind == "radio":
                    entry["options"] = f.options
                    entry["selected"] = (
                        f.options[int(f.value)]
                        if f.value.isdigit() and int(f.value) < len(f.options)
                        else None
                    )
                elif f.kind == "checkbox":
                    entry["checked"] = f.value == "Yes"
                else:
                    entry["value"] = f.value
                entries.append(entry)
            out["sections"].append(
                {"id": key, "title": SECTIONS.get(key, key), "fields": entries}
            )
        return out

    def to_markdown(self) -> str:
        lines = [
            "# Client Brief — extracted from `mog.pdf`",
            "",
            "> Generated by `tools/extract_intake.py`. Do not edit by hand; re-run the",
            "> extractor if the signed PDF changes.",
            "",
            "**Source** HesMartech Client Project Intake Form (5 pages, 104 AcroForm fields)  ",
            "**Signed by** Rob Chansa",
            "",
        ]
        for key, fields in self.grouped():
            title = SECTIONS.get(key, key)
            number = key[1:] if key.startswith("s") and key[1:].isdigit() else None
            lines.append(f"## {number + '. ' if number else ''}{title}")
            lines.append("")
            pending_checkboxes: list[Field] = []

            def flush() -> None:
                if not pending_checkboxes:
                    return
                for c in pending_checkboxes:
                    lines.append(f"- [{'x' if c.value == 'Yes' else ' '}] {c.label}")
                lines.append("")
                pending_checkboxes.clear()

            for f in fields:
                if f.kind == "checkbox":
                    pending_checkboxes.append(f)
                    continue
                flush()
                if f.kind == "radio":
                    lines.append(f"**{f.label}**")
                    lines.append("")
                    sel = int(f.value) if f.value.isdigit() else -1
                    for i, opt in enumerate(f.options):
                        lines.append(f"- {'**→ ' + opt + '**' if i == sel else opt}")
                    lines.append("")
                else:
                    label = f.label or "(continued)"
                    answer = f.value.strip() or "_not answered_"
                    lines.append(f"**{label}**  \n{answer}")
                    lines.append("")
            flush()
        return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args(argv)

    form = IntakeForm(args.pdf)
    answered = sum(1 for f in form.fields if f.answered)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(form.to_dict(), indent=2) + "\n")
        print(f"wrote {args.json}")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(form.to_markdown())
        print(f"wrote {args.markdown}")
    if not (args.json or args.markdown):
        print(form.to_markdown())

    print(f"{len(form.fields)} fields, {answered} answered", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
