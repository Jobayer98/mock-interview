"""Turn an uploaded resume into plain text that is safe to hand to extract_profile.

A resume is untrusted input (I3). Hidden text and lines written to instruct an AI are removed here, before any model sees them.
Detection is best-effort, so the data-block isolation and schema checks in resume/profile.py still apply.
"""
from __future__ import annotations

import logging
import math
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from pypdf import PasswordType, PdfReader
from pypdf.generic import NameObject

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
MAX_PAGES = 6
MAX_CHARS = 30_000
MIN_CHARS = 50
MIN_VISIBLE_FONT_PT = 3.0

_WHITE = 0.95
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_UNIT_SQUARE = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
_INVISIBLE_RENDER_MODES = {3, 7}
_TEXT_SHOW = {b"Tj", b"TJ", b"'", b'"'}
_PATH_BUILDERS = {b"m", b"l", b"c", b"v", b"y", b"re"}
_PATH_PAINTERS = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
_PATH_ENDERS = _PATH_PAINTERS | {b"S", b"s", b"n"}
_W_P, _W_R, _W_TEXT_BOX = qn("w:p"), qn("w:r"), qn("w:txbxContent")
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

_ZERO_WIDTH = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your)\b[^.\n]{0,20}\b(instructions?|prompts?|rules?|directions?)\b"
    r"|\bsystem prompt\b"
    r"|\byou are (now )?an? (ai|assistant|language model|llm|grader|recruiter bot)\b"
    r"|\b(dear|note to( the)?) (ai|llm|chatbot|assistant|model|grader|recruiter bot)\b"
    r"|\b(rate|score|rank|grade|mark)\b[^.\n]{0,30}\b(this (candidate|applicant|resume|cv)|the (candidate|applicant))\b"
    r"|\b(give|award|assign)\b[^.\n]{0,30}\b(full marks|highest (score|rating|rank)|top (score|rating)|perfect score|10/10)\b"
    r"|\b(hire|recommend|shortlist|select) this (candidate|applicant)\b",
    re.IGNORECASE,
)


class ResumeError(ValueError):
    """The file cannot be used. The message is written for the candidate."""


def _strip_instructions(text: str) -> tuple[str, int]:
    """Drop sentences addressed to an AI. Works per sentence so that one bad phrase does not cost a whole run-on line."""
    removed = 0
    kept_lines = []
    for line in text.split("\n"):
        sentences = _SENTENCE_END.split(line)
        kept = [s for s in sentences if not _INSTRUCTION.search(s)]
        removed += len(sentences) - len(kept)
        if kept or not line.strip():
            kept_lines.append(" ".join(kept))
    return "\n".join(kept_lines), removed


def _read_plain_text(path: Path) -> tuple[str, list[str]]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), []
    try:
        return raw.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), []


def _multiply(m: tuple, n: tuple) -> tuple:
    a, b, c, d, e, f = m
    g, h, i, j, k, l = n
    return (a * g + b * i, a * h + b * j, c * g + d * i, c * h + d * j, e * g + f * i + k, e * h + f * j + l)


def _apply(m: tuple, x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _scale(m: tuple) -> float:
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


def _translation(x: float, y: float) -> tuple:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def _is_white(values: list) -> bool:
    """Grey, RGB and CMYK are told apart by operand count. Pattern fills arrive as names and count as not white."""
    if not values or not all(isinstance(v, (int, float)) for v in values):
        return False
    if len(values) == 4:
        return all(float(v) <= 1 - _WHITE for v in values)
    return len(values) in (1, 3) and all(float(v) >= _WHITE for v in values)


def _bounding_box(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _path_points(operator: bytes, operands: list) -> list[tuple[float, float]]:
    numbers = [float(v) for v in operands]
    if operator == b"re":
        x, y, width, height = numbers
        return [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]
    return list(zip(numbers[::2], numbers[1::2]))


@dataclass
class _Graphics:
    ctm: tuple = _IDENTITY
    white_fill: bool = False
    render_mode: int = 0
    font_size: float = 0.0
    leading: float = 0.0


def _remove_hidden_text(page) -> None:
    """Drop text a reader could not see, leaving pypdf's own font decoding to read the rest.

    Follows the drawing operators just far enough to know each run's colour, size and position. White text is kept when it sits on
    something dark, because designer resume templates often use white text on a coloured banner. Form XObjects are not examined.
    """
    content = page.get_contents()
    if content is None:
        return
    left, bottom, right, top = (float(v) for v in page.mediabox)
    resources = page["/Resources"].get_object() if "/Resources" in page else {}
    xobjects = resources["/XObject"].get_object() if "/XObject" in resources else {}
    state, saved = _Graphics(), []
    line_matrix = text_matrix = _IDENTITY
    path: list[tuple[float, float]] = []
    painted: list[tuple[tuple[float, float, float, float], bool]] = []  # bounding box, and whether it is a dark surface
    kept = []

    def hidden() -> bool:
        x, y = _apply(_multiply(text_matrix, state.ctm), 0.0, 0.0)
        size = state.font_size * _scale(text_matrix) * _scale(state.ctm)
        off_page = not (left - 1 <= x <= right + 1 and bottom - 1 <= y <= top + 1)
        if state.render_mode in _INVISIBLE_RENDER_MODES or size < MIN_VISIBLE_FONT_PT or off_page:
            return True
        if not state.white_fill:
            return False
        for (x0, y0, x1, y1), dark in reversed(painted):  # the most recent surface painted under this point decides
            if x0 <= x <= x1 and y0 <= y <= y1:
                return not dark
        return True

    for operands, operator in content.operations:
        if operator == b"q":
            saved.append(replace(state))
        elif operator == b"Q" and saved:
            state = saved.pop()
        elif operator == b"cm":
            state.ctm = _multiply(tuple(float(v) for v in operands), state.ctm)
        elif operator in (b"g", b"rg", b"k", b"sc", b"scn"):
            state.white_fill = _is_white(operands)
        elif operator == b"cs":
            state.white_fill = False
        elif operator in _PATH_BUILDERS:
            path += [_apply(state.ctm, x, y) for x, y in _path_points(operator, operands)]
        elif operator == b"Do" and operands[0] in xobjects and xobjects[operands[0]].get_object().get("/Subtype") == "/Image":
            painted.append((_bounding_box([_apply(state.ctm, x, y) for x, y in _UNIT_SQUARE]), True))
        elif operator == b"BT":
            line_matrix = text_matrix = _IDENTITY
        elif operator == b"Tm":
            line_matrix = text_matrix = tuple(float(v) for v in operands)
        elif operator in (b"Td", b"TD"):
            state.leading = -float(operands[1]) if operator == b"TD" else state.leading
            line_matrix = text_matrix = _multiply(_translation(float(operands[0]), float(operands[1])), line_matrix)
        elif operator == b"T*":
            line_matrix = text_matrix = _multiply(_translation(0.0, -state.leading), line_matrix)
        elif operator == b"TL":
            state.leading = float(operands[0])
        elif operator == b"Tf":
            state.font_size = abs(float(operands[1]))
        elif operator == b"Tr":
            state.render_mode = int(operands[0])
        elif operator in _TEXT_SHOW:
            if operator in (b"'", b'"'):
                line_matrix = text_matrix = _multiply(_translation(0.0, -state.leading), line_matrix)
            if hidden():
                continue
        if operator in _PATH_PAINTERS and path:
            painted.append((_bounding_box(path), not state.white_fill))
        if operator in _PATH_ENDERS:
            path = []
        kept.append((operands, operator))

    if len(kept) != len(content.operations):  # most resumes hide nothing, and those pages are left exactly as they were
        content.operations = kept
        page[NameObject("/Contents")] = content


def _read_pdf(path: Path) -> tuple[str, list[str]]:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise ResumeError("This PDF is password-protected. Please upload a copy without a password.")
        if len(reader.pages) > MAX_PAGES:
            raise ResumeError(f"This resume has {len(reader.pages)} pages. Please upload one with at most {MAX_PAGES}.")
        pages, hidden_characters = [], 0
        for page in reader.pages:
            original = len(page.extract_text())
            _remove_hidden_text(page)
            pages.append(page.extract_text())
            hidden_characters += max(original - len(pages[-1]), 0)
    except ResumeError:
        raise
    except Exception as error:  # a damaged or hostile file can fail anywhere inside the parser
        logger.warning("resume: PDF parsing failed with %s", type(error).__name__)
        raise ResumeError("Could not read this PDF. It may be damaged. Please try exporting it again.") from error
    warnings = [f"Removed about {hidden_characters} characters of hidden text (invisible, white, tiny or off the page)."] if hidden_characters else []
    return "\n".join(pages), warnings


def _font_chain(run: Run, paragraph: Paragraph):
    """Where a run's formatting can come from, nearest first."""
    yield run.font
    if run.style is not None:
        yield run.style.font
    style = paragraph.style
    while style is not None:
        yield style.font
        style = style.base_style


def _on_coloured_surface(run_element) -> bool:
    """White text is fine on shading, a highlight, or inside a text box, whose fill we cannot see."""
    for element in chain([run_element], run_element.iterancestors()):
        if element.tag == _W_TEXT_BOX:
            return True
        properties = [element.find(tag) for tag in (qn("w:rPr"), qn("w:pPr"), qn("w:tcPr"))]
        for found in filter(lambda p: p is not None, properties):
            shading = found.find(qn("w:shd"))
            if found.find(qn("w:highlight")) is not None or (shading is not None and shading.get(qn("w:fill"), "auto").upper() not in ("AUTO", "FFFFFF")):
                return True
    return False


def _run_is_hidden(run: Run, paragraph: Paragraph) -> bool:
    size = colour = None
    for font in _font_chain(run, paragraph):
        if font.hidden:
            return True
        size = size if size is not None else font.size
        colour = colour if colour is not None else font.color.rgb
    if size is not None and size.pt < MIN_VISIBLE_FONT_PT:
        return True
    return colour is not None and all(channel >= _WHITE * 255 for channel in colour) and not _on_coloured_surface(run._element)


def _docx_paragraph_text(element, parent) -> tuple[str, int]:
    """The visible text of one paragraph, and how many characters were hidden. Nested text-box paragraphs are read on their own turn."""
    paragraph = Paragraph(element, parent)
    visible, hidden = [], 0
    for run_element in element.iter(_W_R):
        if next(run_element.iterancestors(_W_P)) is not element:
            continue
        run = Run(run_element, paragraph)
        if _run_is_hidden(run, paragraph):
            hidden += len(run.text)
        else:
            visible.append(run.text)
    return "".join(visible), hidden


def _read_docx(path: Path) -> tuple[str, list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise ResumeError("This file is too large once opened. Please upload a simpler copy.")
        document = Document(str(path))
        # Headers come first: the candidate's name is often there, and redaction looks for it on the first line.
        parts = [part._element for section in document.sections for part in (section.header, section.footer) if not part.is_linked_to_previous]
        lines, hidden_characters = [], 0
        for root in [*parts, document.element.body]:
            for element in root.iter(_W_P):
                if any(ancestor.tag == _MC_FALLBACK for ancestor in element.iterancestors()):  # the same text box, saved again for old readers
                    continue
                text, hidden = _docx_paragraph_text(element, document)
                lines.append(text)
                hidden_characters += hidden
    except ResumeError:
        raise
    except Exception as error:  # a damaged or hostile file can fail anywhere inside the parser
        logger.warning("resume: Word parsing failed with %s", type(error).__name__)
        raise ResumeError("Could not read this Word file. It may be damaged. Please try saving it again.") from error
    warnings = [f"Removed about {hidden_characters} characters of hidden text (hidden, white or tiny)."] if hidden_characters else []
    return "\n".join(lines), warnings


_READERS: dict[str, Callable[[Path], tuple[str, list[str]]]] = {".txt": _read_plain_text, ".pdf": _read_pdf, ".docx": _read_docx}


def _finish(text: str, warnings: list[str]) -> tuple[str, list[str]]:
    text = _CONTROL.sub("", _ZERO_WIDTH.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    if len(text) > MAX_CHARS:
        raise ResumeError(f"This resume is too long. Please keep it under about {MAX_CHARS // 1000},000 characters.")
    text, removed = _strip_instructions(text)
    if removed:
        plural = "passage" if removed == 1 else "passages"
        warnings.append(f"Removed {removed} {plural} that looked like instructions to an AI.")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.split("\n"))).strip()
    if len(text) < MIN_CHARS:
        raise ResumeError("This file has almost no readable text. If it is a scan or an image, please upload a file with selectable text.")
    for warning in warnings:  # counts only: resume content must not reach the logs
        logger.warning("resume: %s", warning)
    return text, warnings


def load_resume(path: str | Path) -> tuple[str, list[str]]:
    """Return (text, warnings). Raises ResumeError, with a message for the candidate, if the file cannot be used."""
    path = Path(path)
    if not path.is_file():
        raise ResumeError(f"Could not find the file {path.name}.")
    if path.stat().st_size > MAX_BYTES:
        raise ResumeError(f"This file is too large. Please upload a resume under {MAX_BYTES // (1024 * 1024)} MB.")
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ResumeError("Please upload a PDF, a Word file (.docx) or a plain text file (.txt).")
    text, warnings = reader(path)
    return _finish(text, warnings)
