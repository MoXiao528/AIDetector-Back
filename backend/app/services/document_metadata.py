"""Locate document metadata without changing the text supplied to the detector."""

import re
from html import escape
from html.parser import HTMLParser


_HEADINGS = {f"h{level}" for level in range(1, 7)}
_BLOCKS = _HEADINGS | {"p", "div", "section", "article", "main", "aside", "header", "footer", "blockquote", "pre", "li", "ul", "ol", "table", "tr", "td", "th"}
_IGNORED = {"script", "style", "noscript"}
_BYLINE = re.compile(r"(?:作者\s*[:：]|author\s*[:：])\s*(\S.*)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[。.!?！？；;][”’\"'）】》〉)\]]*$")


def document_paragraphs(text: str) -> list[str]:
    return [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]


def comparable_text(text: str) -> str:
    return " ".join(text.split())


class _HeadingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[tuple[str, int | None]] = []
        self.heading: tuple[str, int] | None = None
        self.closed: set[int] = set()
        self.invalid: set[int] = set()
        self.sequence = 0
        self.ignored: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            return
        if tag in _IGNORED:
            self.ignored = tag
            return
        if tag in _BLOCKS:
            self.parts.append(("\n", None))
            if self.heading:
                self.invalid.add(self.heading[1])
            elif tag in _HEADINGS:
                self.sequence += 1
                self.heading = (tag, self.sequence)
        elif tag == "br":
            self.parts.append(("\n", None))

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored:
                self.ignored = None
            return
        if self.heading and tag == self.heading[0]:
            self.closed.add(self.heading[1])
            self.heading = None
        elif self.heading and tag in _BLOCKS:
            self.invalid.add(self.heading[1])
        if tag in _BLOCKS:
            self.parts.append(("\n", None))

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append((data, self.heading[1] if self.heading else None))

    def paragraphs(self) -> list[tuple[str, bool]]:
        rows: list[tuple[str, bool]] = []
        pieces: list[str] = []
        heading_ids: set[int | None] = set()

        def flush():
            text = "".join(pieces)
            if text.strip():
                rows.append((text, len(heading_ids) == 1 and heading_ids <= self.closed - self.invalid))
            pieces.clear()
            heading_ids.clear()

        for text, heading_id in self.parts:
            for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
                if index:
                    flush()
                pieces.append(line)
                if line.strip():
                    heading_ids.add(heading_id)
        flush()
        return rows


def metadata_paragraph_indexes(text: str, editor_html: str | None = None) -> set[int]:
    paragraphs = document_paragraphs(text)
    indexes: set[int] = set()
    if len(paragraphs) >= 3:
        title, byline = (paragraph.strip() for paragraph in paragraphs[:2])
        author = _BYLINE.fullmatch(byline)
        if (
            0 < len("".join(title.split())) <= 60
            and not _SENTENCE_END.search(title)
            and len("".join(byline.split())) <= 80
            and author
            and not re.search(r"[。！？!?；;：:]", author[1])
        ):
            indexes.update((0, 1))
    if editor_html:
        parser = _HeadingParser()
        parser.feed(editor_html)
        parser.close()
        html_rows = parser.paragraphs()
        # Require complete positional alignment; repeated text or unrelated HTML cannot label another paragraph.
        if [comparable_text(row[0]) for row in html_rows] == [comparable_text(p) for p in paragraphs]:
            indexes.update(index for index, (_, heading) in enumerate(html_rows) if heading)
    return indexes


def paragraph_highlight_html(text: str, segment_id: str, segment_type: str, probability: float) -> str:
    if segment_type == "metadata":
        return "".join(f"<p>{escape(line)}</p>" for line in text.split("\n") if line.strip())
    if segment_type == "too_short":
        classes = "bg-slate-100 text-slate-600 ring-1 ring-slate-200"
    else:
        classes = "bg-emerald-100 text-emerald-900"
        for minimum, palette in ((0.8, "rose"), (0.6, "orange"), (0.4, "amber"), (0.2, "lime")):
            if probability >= minimum:
                classes = f"bg-{palette}-100 text-{palette}-900"
                break
    return "".join(
        f'<p><span class="highlight-chip {classes}" data-sentence-id="{escape(segment_id, quote=True)}">{escape(line)}</span></p>'
        for line in text.split("\n") if line.strip()
    )
