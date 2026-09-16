"""Loom counts code points; LSP counts UTF-16 code units. A file with one astral character, or with CRLF line endings, is where the two part company."""

from __future__ import annotations

from loom_lsp.encoding import Mapper, utf16_len

ASTRAL = "\U0001d539"  # a double-struck B, one code point and two UTF-16 units


def test_astral_characters_shift_every_column_after_them() -> None:
    text = f"let ${ASTRAL}$ be a widget\nsecond line\n"
    m = Mapper(text)
    assert utf16_len(text.split("\n")[0]) == len(text.split("\n")[0]) + 1

    at_be = text.index("be")
    line, char = m.position(at_be)
    assert (line, char) == (0, at_be + 1)  # one more unit than code points, because of the astral character
    assert m.offset(line, char) == at_be

    # and a position before the astral character is unaffected
    assert m.position(text.index("let")) == (0, 0)


def test_code_point_encoding_is_honoured_when_negotiated() -> None:
    text = f"${ASTRAL}$ x\n"
    m = Mapper(text, encoding="utf-32")
    assert m.position(text.index("x")) == (0, text.index("x"))


def test_crlf_text_is_normalised_the_way_loom_normalises_it() -> None:
    m = Mapper("alpha\r\nbeta\r\ngamma\r\n")
    assert m.text == "alpha\nbeta\ngamma\n"
    assert m.position(m.text.index("beta")) == (1, 0)
    assert m.position(m.text.index("gamma")) == (2, 0)
    assert m.offset(2, 0) == m.text.index("gamma")


def test_a_position_past_the_end_of_a_line_clamps_to_it() -> None:
    m = Mapper("ab\ncd\n")
    assert m.offset(0, 99) == 2
    assert m.offset(9, 0) == len("ab\ncd\n")
