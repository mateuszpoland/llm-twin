"""Split a document into chunks on boundaries the author actually made.

Headings and blank lines, never a fixed-size sliding window. A window with
overlap means inserting one sentence shifts every boundary below it, every
content hash changes, and the diff reports the whole document as new - which is
exactly the failure content-addressing exists to prevent.

Code is detected and, by default, dropped. The tech articles are 25-42%
unfenced code, and a chunk that is half broken pseudocode teaches the twin to
emit broken pseudocode. Code is also the least style-bearing part of the
writing. Flip `keep_code` if that call turns out wrong.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from twin.ingest.models import normalize

TARGET_WORDS = 300
MAX_WORDS = 500
MIN_WORDS = 40

# A line that is only dashes is a section break in these articles.
SECTION_BREAK = re.compile(r"^\s*-{3,}\s*$")
FENCE = re.compile(r"^\s*```")

# Signals that a line is code rather than prose. None is conclusive alone,
# which is why a block is judged on the proportion of its lines that match.
CODE_LINE = re.compile(
    r"^\s{4,}\S"  # indented
    r"|[{};]\s*$"  # ends in a brace or semicolon
    r"|^\s*(?:def|class|import|from|return|public|private|function|const|let|var)\b"
    r"|^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b"
    r"|^\s*(?:\$|>>>|\#\!|//|<\?php)"
    r"|^\s*[\w_]+\s*=\s*[\w_\[\{\"']"  # assignment
    r"|^\s*[\w_]+\([^)]*\)\s*[:{]?\s*$",  # bare call / signature
    re.MULTILINE,
)
CODE_SHARE = 0.4

# Sentence end: terminal punctuation, whitespace, then something that starts a
# new sentence. Used only as a last resort on blocks with no internal newlines.
SENTENCE_END = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u017b\u0179\"\u201c])"
)


@dataclass
class Block:
    """One paragraph-ish unit, before chunks are assembled."""

    text: str
    is_code: bool
    is_break: bool = False

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Chunk:
    ord: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())

    def chunk_id(self, document_id: str) -> bytes:
        """Identity from content, never position.

        `ord` is deliberately absent: a chunk that moves from slot 1 to slot 3
        must keep the same id, or its embedding is thrown away for nothing.
        """
        return hashlib.sha256(f"{document_id}:{normalize(self.text)}".encode()).digest()


def looks_like_code(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    hits = sum(1 for ln in lines if CODE_LINE.match(ln))
    return hits / len(lines) >= CODE_SHARE


def _subdivide(body: str) -> list[str]:
    """Break an oversized block down, still on boundaries the author made.

    Not every file separates paragraphs with a blank line: the essays mostly use
    a single newline, which made whole sections arrive here as one 2,456-word
    block. So fall back to line breaks, and only then to sentence ends.

    This stays stable under editing for the same reason blank-line splitting
    does - the boundaries are in the text, so changing one line changes one
    piece rather than shifting every boundary after it.
    """
    if len(body.split()) <= MAX_WORDS:
        return [body]

    units = [ln.strip() for ln in body.split("\n") if ln.strip()]
    if len(units) == 1:
        units = [u.strip() for u in SENTENCE_END.split(body) if u.strip()]

    out: list[str] = []
    buf: list[str] = []
    words = 0
    for unit in units:
        n = len(unit.split())
        if words and words + n > MAX_WORDS:
            out.append("\n".join(buf))
            buf, words = [], 0
        buf.append(unit)
        words += n
        if words >= TARGET_WORDS:
            out.append("\n".join(buf))
            buf, words = [], 0
    if buf:
        out.append("\n".join(buf))
    return out or [body]


def to_blocks(text: str) -> list[Block]:
    """Blank lines separate blocks; `---` and ``` fences are hard boundaries."""
    blocks: list[Block] = []
    in_fence = False
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if not body:
            return
        code = looks_like_code(body)
        for piece in _subdivide(body):
            blocks.append(Block(text=piece, is_code=code))

    for raw in text.splitlines():
        if FENCE.match(raw):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            buffer.append(raw)
            continue
        if SECTION_BREAK.match(raw):
            flush()
            blocks.append(Block(text="", is_code=False, is_break=True))
            continue
        if not raw.strip():
            flush()
            continue
        buffer.append(raw)

    flush()
    return blocks


def split(text: str, *, keep_code: bool = False) -> list[Chunk]:
    """Group blocks into chunks of roughly TARGET_WORDS, never splitting one.

    A block is the atom. Chunk boundaries therefore always fall where the author
    put a paragraph break, so an edit inside one paragraph changes exactly one
    chunk rather than cascading through the rest of the document.
    """
    blocks = to_blocks(text)
    if not keep_code:
        blocks = [b for b in blocks if not b.is_code]

    chunks: list[Chunk] = []
    buffer: list[str] = []
    words = 0

    def flush() -> None:
        nonlocal words
        body = "\n\n".join(buffer).strip()
        buffer.clear()
        words = 0
        if body:
            chunks.append(Chunk(ord=len(chunks), text=body))

    for block in blocks:
        if block.is_break:
            # A section break ends the current chunk: sections are the author's
            # own structure, and respecting them keeps chunks topically whole.
            flush()
            continue
        if words and words + block.words > MAX_WORDS:
            flush()
        buffer.append(block.text)
        words += block.words
        if words >= TARGET_WORDS:
            flush()

    flush()

    # A trailing scrap is appended to its predecessor rather than emitted alone:
    # a 6-word chunk is noise in retrieval and a useless training target.
    if len(chunks) > 1 and chunks[-1].words < MIN_WORDS:
        tail = chunks.pop()
        chunks[-1] = Chunk(ord=chunks[-1].ord, text=f"{chunks[-1].text}\n\n{tail.text}")

    return chunks
