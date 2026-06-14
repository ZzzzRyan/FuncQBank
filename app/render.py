"""Render question stems/options to safe HTML.

Content is plain text containing LaTeX math in ``$...$`` / ``$$...$$`` and
optional ``**bold**`` emphasis. We HTML-escape everything (XSS-safe), convert
``**bold**`` to <strong> *outside* math spans, and leave math delimiters intact
so KaTeX auto-render (client-side) typesets them. KaTeX reads ``textContent``,
which the browser un-escapes, so escaping inside math is harmless and correct.
"""
from __future__ import annotations

import html
import re

from markupsafe import Markup

_MATH_RE = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _render_nonmath(seg: str) -> str:
    seg = html.escape(seg)
    seg = _BOLD_RE.sub(r"<strong>\1</strong>", seg)
    return seg.replace("\n", "<br>")


def render_rich(text: str | None) -> Markup:
    if not text:
        return Markup("")
    parts: list[str] = []
    last = 0
    for m in _MATH_RE.finditer(text):
        parts.append(_render_nonmath(text[last : m.start()]))
        parts.append(html.escape(m.group(0)))  # keep $...$ delimiters for KaTeX
        last = m.end()
    parts.append(_render_nonmath(text[last:]))
    return Markup("".join(parts))
