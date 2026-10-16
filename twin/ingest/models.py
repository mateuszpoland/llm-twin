"""What a parsed source looks like before it reaches the database.

This is the gate. Telegram's JSON and WhatsApp's text are hostile input - a
message's `text` is sometimes a string and sometimes a list of fragments, dates
are sometimes missing, names are sometimes absent. Everything downstream is
written assuming clean, typed values, so the mess stops here.

Pydantic is doing shape-checking. It is not doing
persistence - that is SQLAlchemy's job in twin/warehouse. Two thin layers, each
good at one thing, instead of one class trying to be both.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import warnings
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# `register` is the agreed domain term and matches the database column, but it
# also names a classmethod inherited from ABCMeta. The shadowing is harmless
# here - nothing calls ParsedDocument.register() - so silence just this warning
# rather than renaming the field away from the schema.
warnings.filterwarnings("ignore", message='Field name "register".*shadows an attribute', category=UserWarning)


class Twin(StrEnum):
    ARTICLES = "articles"
    CHAT = "chat"


class Register(StrEnum):
    TECH = "tech"
    ESSAY = "essay"
    CASUAL = "casual"


class Source(StrEnum):
    LOCAL = "local"
    MEDIUM = "medium"
    GDRIVE = "gdrive"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"


class Origin(StrEnum):
    """Who actually wrote this. Set at ingestion, never guessed later.

    The defence against the twin training on its own drafts: once the agent
    starts producing text you edit and feed back, this column is the only thing
    that can tell your prose from its prose.
    """

    HUMAN = "human_written"
    MODEL_EDITED = "model_drafted_edited"
    MODEL = "model_drafted"


_WS_RUN = re.compile(r"[ \t]+")
_BLANK_RUN = re.compile(r"\n{3,}")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalize(text: str) -> str:
    """Flatten the differences that are not real differences.

    Hashes are computed over this, not over raw text. Without it, re-saving a
    file in a different editor - which rewrites line endings or swaps straight
    quotes for curly ones - reads as a total rewrite, and the whole document
    gets re-embedded and re-sent to an LLM for nothing.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_QUOTES)
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def content_hash(text: str) -> bytes:
    """The document-level skip gate: unchanged hash means do nothing at all."""
    return hashlib.sha256(normalize(text).encode("utf-8")).digest()


class Message(BaseModel):
    """One message in a conversation, already flattened and typed."""

    author: str
    text: str
    sent_at: datetime | None = None
    is_mine: bool = False

    @field_validator("text")
    @classmethod
    def _must_have_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty message text")
        return v


class ParsedDocument(BaseModel):
    """One ingestible source: an article file, or a whole conversation.

    `source_ref` is the identity anchor - a filename for articles, a numeric
    chat id for conversations. It is what the G1 manifest is keyed on and what
    the warehouse upserts on, so it must survive the content being edited.
    """

    # `register` collides with ABCMeta.register on BaseModel. The field name is
    # the agreed domain term and matches the database column, so keep it and
    # tell Pydantic the shadowing is deliberate.
    model_config = ConfigDict(protected_namespaces=(), ignored_types=())

    twin: Twin
    register: Register
    source: Source
    source_ref: str = Field(min_length=1)
    origin: Origin = Origin.HUMAN
    title: str | None = None
    content: str
    lang: str | None = None
    meta: dict = Field(default_factory=dict)
    messages: list[Message] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _must_have_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty document content")
        return v

    @property
    def hash(self) -> bytes:
        return content_hash(self.content)
