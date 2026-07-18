"""Chunk everything under content/ (.md, .pdf, and images), embed each chunk via
Gemini, caption images via Groq, and load it all into the Neon Postgres `chunks`
table (pgvector).

Run this manually whenever content/ changes:

    python scripts/ingest.py
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pypdf import PdfReader  # noqa: E402

from app.db import get_pool  # noqa: E402
from app.embeddings import embed_documents  # noqa: E402
from app.vision import MIME_TYPES, caption_image  # noqa: E402
from init_db import init_db  # noqa: E402

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "caption_cache.json"

MAX_CHUNK_CHARS = 800

TEXT_EXTENSIONS = {".md", ".pdf"}
IMAGE_EXTENSIONS = set(MIME_TYPES)


def load_markdown(path: Path) -> str:
    return path.read_text()


def load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


TEXT_LOADERS = {".md": load_markdown, ".pdf": load_pdf}


def split_oversized(paragraph: str) -> list[str]:
    """PDF extraction sometimes yields one huge paragraph with no blank lines
    (no natural break points) — fall back to splitting on whitespace boundaries
    so no single chunk is too large for meaningful retrieval."""
    if len(paragraph) <= MAX_CHUNK_CHARS:
        return [paragraph]
    words = paragraph.split()
    pieces, current = [], ""
    for word in words:
        if current and len(current) + len(word) + 1 > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str) -> list[str]:
    """Split on blank lines (paragraphs), dropping headings/quote callouts and
    empties, then guard against any single paragraph being too large."""
    paragraphs = [p.strip() for p in text.split("\n\n")]
    paragraphs = [
        p for p in paragraphs if p and not p.startswith("#") and not p.startswith(">")
    ]
    return [piece for p in paragraphs for piece in split_oversized(p)]


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def get_caption(path: Path, cache: dict[str, str]) -> str:
    """Caches by content hash (not filename) so image captioning only runs on
    genuinely new/changed images — without this, every re-run recaptions
    everything from scratch."""
    image_bytes = path.read_bytes()
    digest = hashlib.sha256(image_bytes).hexdigest()
    if digest in cache:
        print(f"Using cached caption for {path.name}")
        return cache[digest]

    caption = caption_image(image_bytes)
    cache[digest] = caption
    save_cache(cache)  # incremental — survives a mid-run crash
    print(f"Captioned {path.name}: {caption[:100]}...")
    return caption


def main() -> None:
    chunks: list[dict] = []
    cache = load_cache()
    all_extensions = TEXT_EXTENSIONS | IMAGE_EXTENSIONS
    paths = sorted(
        p for p in CONTENT_DIR.iterdir() if p.suffix.lower() in all_extensions
    )
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in TEXT_LOADERS:
            text = TEXT_LOADERS[suffix](path)
            for chunk in chunk_text(text):
                chunks.append({"text": chunk, "source": path.name})
        else:
            caption = get_caption(path, cache)
            chunks.append({"text": caption, "source": path.name})

    if not chunks:
        supported = ", ".join(sorted(all_extensions))
        raise SystemExit(
            f"No content found under {CONTENT_DIR} — add a file ({supported}) first."
        )

    vectors = embed_documents(chunks)
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["vector"] = vector

    init_db()
    with get_pool().connection() as conn:
        conn.execute("TRUNCATE chunks")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (source, text, embedding) VALUES (%s, %s, %s)",
                [(c["source"], c["text"], c["vector"]) for c in chunks],
            )
        conn.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
        conn.execute(
            "CREATE INDEX chunks_embedding_hnsw ON chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )

    by_source = {}
    for chunk in chunks:
        by_source[chunk["source"]] = by_source.get(chunk["source"], 0) + 1
    summary = ", ".join(f"{src}: {n}" for src, n in by_source.items())
    print(f"Loaded {len(chunks)} chunks into Postgres ({summary})")

    get_pool().close()


if __name__ == "__main__":
    main()
