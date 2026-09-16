"""Position encodings. Loom counts code points into CRLF-normalised text; LSP counts UTF-16 code units by default.

The two agree for every character in the Basic Multilingual Plane and disagree for the rest, so a file with one astral character shifts every column after it by one. A CRLF file disagrees earlier still, because loom's offsets are into text whose line endings are already normalised. Both conversions are here, and nothing else in the server does arithmetic on a column.
"""

from __future__ import annotations

from bisect import bisect_right


def utf16_len(text: str) -> int:
    """The length of `text` in UTF-16 code units, which is what an LSP column counts by default."""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


class Mapper:
    """Offsets to and from LSP positions for one buffer.

    The buffer's text is normalised as loom normalises it (CRLF and CR to LF), so an offset from loom indexes it directly; the original line lengths are kept so that a position the client sends, which counts the client's own line endings, still lands in the right place.
    """

    def __init__(self, text: str, *, encoding: str = "utf-16") -> None:
        self.raw = text
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.encoding = encoding
        self.line_starts = [0]
        for i, ch in enumerate(self.text):
            if ch == "\n":
                self.line_starts.append(i + 1)

    def line_of(self, offset: int) -> int:
        """Zero-based line of a code-point offset."""
        return max(0, bisect_right(self.line_starts, min(offset, len(self.text))) - 1)

    def _units(self, s: str) -> int:
        return utf16_len(s) if self.encoding == "utf-16" else len(s)

    def position(self, offset: int) -> tuple[int, int]:
        """(line, character) for a code-point offset, in the negotiated encoding."""
        offset = max(0, min(offset, len(self.text)))
        line = self.line_of(offset)
        return line, self._units(self.text[self.line_starts[line] : offset])

    def offset(self, line: int, character: int) -> int:
        """The code-point offset of an LSP position. A position past the end of its line clamps to the line's end."""
        if line < 0:
            return 0
        if line >= len(self.line_starts):
            return len(self.text)
        start = self.line_starts[line]
        end = self.line_starts[line + 1] - 1 if line + 1 < len(self.line_starts) else len(self.text)
        if self.encoding != "utf-16":
            return min(start + character, end)
        used = 0
        for i in range(start, end):
            if used >= character:
                return i
            used += 2 if ord(self.text[i]) > 0xFFFF else 1
        return end
