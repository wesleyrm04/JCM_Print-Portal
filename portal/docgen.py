"""Typed-document generator: plain text -> real PDF (Helvetica 11, A4, 1" margins) + DOCX.
Helvetica has the same character widths as Arial, so line breaks match what Arial 11 gives.
Pure Python, no fonts or internet required."""
import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape as xml_escape

FONT_SIZE = 11.0
LEADING = 13.0
PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 72.0
TEXT_W = PAGE_W - 2 * MARGIN
LINES_PER_PAGE = int((PAGE_H - 2 * MARGIN) // LEADING)

# Helvetica advance widths (1/1000 em) for ASCII 32..126
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
      667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
assert len(_W) == 95
WIDTHS = {chr(32 + i): w for i, w in enumerate(_W)}
_SUBS = {"\u20b1": "PHP ", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u2022": "*", "\t": "    ", "\u00a0": " "}


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for k, v in _SUBS.items():
        text = text.replace(k, v)
    out = []
    for ch in text:
        if ch == "\n" or 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            try:
                ch.encode("cp1252")
                out.append(ch)                        # n-tilde, accents, etc. are in WinAnsi
            except UnicodeEncodeError:
                base = unicodedata.normalize("NFKD", ch)[:1]
                out.append(base if base and ord(base) < 127 else "?")
    return "".join(out)


def _w(ch: str) -> float:
    if ch in WIDTHS:
        return WIDTHS[ch] / 1000.0 * FONT_SIZE
    base = unicodedata.normalize("NFKD", ch)[:1]
    return WIDTHS.get(base, 556) / 1000.0 * FONT_SIZE


def _text_w(s: str) -> float:
    return sum(_w(c) for c in s)


def wrap_lines(text: str):
    lines = []
    for para in clean_text(text).split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for token in re.findall(r"\S+\s*", para):
            cand = cur + token
            if _text_w(cand.rstrip()) <= TEXT_W:
                cur = cand
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            while _text_w(token.rstrip()) > TEXT_W:   # a single very long word
                cut = len(token)
                while cut > 1 and _text_w(token[:cut]) > TEXT_W:
                    cut -= 1
                lines.append(token[:cut])
                token = token[cut:]
            cur = token
        lines.append(cur.rstrip())
    return lines


def paginate(text: str):
    lines = wrap_lines(text)
    if not lines:
        lines = [""]
    return [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)]


def count_pages(text: str) -> int:
    return len(paginate(text))


def _pdf_escape(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(text: str) -> bytes:
    pages = paginate(text)
    objs = []                                       # index i -> object number i+1
    n_pages = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    y0 = PAGE_H - MARGIN - FONT_SIZE
    for i, lines in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                     f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>").encode())
        parts = [f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {MARGIN} {y0:.2f} Td".encode()]
        for ln in lines:
            parts.append(b"(" + _pdf_escape(ln) + b") Tj T*")
        parts.append(b"ET")
        stream = b"\n".join(parts)
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(text: str) -> bytes:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u20b1", "\u20b1")
    paras = []
    for line in text.split("\n"):
        run = (f'<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
               f'<w:sz w:val="22"/></w:rPr><w:t xml:space="preserve">{xml_escape(line)}</w:t></w:r>')
        paras.append(f'<w:p><w:pPr><w:spacing w:after="0" w:line="259" w:lineRule="auto"/></w:pPr>{run}</w:p>')
    body = "".join(paras) + ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                             '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
                             'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()
