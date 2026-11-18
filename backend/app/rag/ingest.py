"""Knowledge-base ingestion: Markdown (YAML front matter) → citable chunks.

Each `##`/`###` section becomes one or more chunks (`MarkdownHeaderTextSplitter`, then a
recursive splitter at ~800 chars with 100 overlap for long sections). Every chunk carries its
citation (`contracts/technova §2.1`) and the document's metadata, and an id derived from its
content, so re-indexing is idempotent: unchanged chunks keep their ids, changed ones get new ids.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.config import get_settings

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION_NO = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")


def default_knowledge_dir() -> Path:
    configured = get_settings().knowledge_dir
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data" / "knowledge"


@dataclass
class KnowledgeDoc:
    source: str  # "ap-policy", "contracts/technova"
    title: str
    doc_type: str
    meta: dict[str, str]
    body: str
    sections: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str  # what gets embedded and searched: heading path + section text
    source: str
    section: str  # "2.1"; "" for text before the first numbered heading
    heading: str
    doc_type: str
    vendor_id: str  # "" for documents that aren't vendor-specific
    valid_from: date | None
    valid_to: date | None
    part: int  # index within a long section

    @property
    def citation(self) -> str:
        return f"{self.source} §{self.section}" if self.section else self.source

    def to_document(self) -> Document:
        return Document(
            id=self.id,
            page_content=self.text,
            metadata={
                "source": self.source,
                "section": self.section,
                "citation": self.citation,
                "heading": self.heading,
                "doc_type": self.doc_type,
                "vendor_id": self.vendor_id,
                "valid_from": self.valid_from.isoformat() if self.valid_from else "",
                "valid_to": self.valid_to.isoformat() if self.valid_to else "",
                "part": self.part,
            },
        )

    @classmethod
    def from_document(cls, doc: Document) -> "Chunk":
        m = doc.metadata
        return cls(
            id=str(doc.id),
            text=doc.page_content,
            source=m["source"],
            section=m["section"],
            heading=m["heading"],
            doc_type=m["doc_type"],
            vendor_id=m.get("vendor_id") or "",
            valid_from=date.fromisoformat(m["valid_from"]) if m.get("valid_from") else None,
            valid_to=date.fromisoformat(m["valid_to"]) if m.get("valid_to") else None,
            part=int(m.get("part", 0)),
        )


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta, text.replace("\r\n", "\n")[m.end() :]


def load_doc(path: Path, root: Path) -> KnowledgeDoc:
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    source = path.relative_to(root).with_suffix("").as_posix()
    return KnowledgeDoc(
        source=source,
        title=meta.get("title", source),
        doc_type=meta.get("doc_type", "other"),
        meta=meta,
        body=body,
    )


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def chunk_doc(doc: KnowledgeDoc) -> list[Chunk]:
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")], strip_headers=True
    )
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks: list[Chunk] = []
    for part in header_splitter.split_text(doc.body):
        headings = [part.metadata[h] for h in ("h1", "h2", "h3") if h in part.metadata]
        deepest = next((h for h in reversed(headings) if _SECTION_NO.match(h)), None)
        match = _SECTION_NO.match(deepest) if deepest else None
        section = match.group(1).rstrip(".") if match else ""
        heading = match.group(2) if match else (headings[-1] if headings else doc.title)
        path = " › ".join([doc.title, *headings[1:]]) if headings else doc.title
        pieces = splitter.split_text(part.page_content) or [""]
        for i, piece in enumerate(pieces):
            text = f"{path}\n{piece}".strip()
            digest = hashlib.sha256(f"{doc.source}|{section}|{i}|{text}".encode()).hexdigest()
            chunks.append(
                Chunk(
                    id=digest[:32],
                    text=text,
                    source=doc.source,
                    section=section,
                    heading=heading,
                    doc_type=doc.doc_type,
                    vendor_id=doc.meta.get("vendor_id", ""),
                    valid_from=_date(doc.meta.get("valid_from")),
                    valid_to=_date(doc.meta.get("valid_to")),
                    part=i,
                )
            )
    return chunks


def load_knowledge(root: Path | None = None) -> list[Chunk]:
    """Every chunk of every Markdown document under the knowledge directory."""
    base = root or default_knowledge_dir()
    out: list[Chunk] = []
    for path in sorted(base.rglob("*.md")):
        out.extend(chunk_doc(load_doc(path, base)))
    return out
