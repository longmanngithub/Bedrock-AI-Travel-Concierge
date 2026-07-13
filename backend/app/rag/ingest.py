"""Build the Chroma vector store from the curated knowledge base.

Run once (and whenever the knowledge/ docs change):

    cd backend
    python -m app.rag.ingest

Pipeline (all LangChain): load .md files -> split into chunks -> embed with a
local sentence-transformers model (free, offline, no API quota) -> persist to
Chroma on disk. The crew's `knowledge_base` tool then retrieves from this store.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import get_settings

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"


def load_documents():
    docs = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        loaded = TextLoader(str(path), encoding="utf-8").load()
        for d in loaded:
            d.metadata["source"] = path.stem  # e.g. "tokyo", "budgeting"
        docs.extend(loaded)
    return docs


def main() -> None:
    settings = get_settings()
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    docs = load_documents()
    if not docs:
        raise SystemExit(f"No .md documents found in {KNOWLEDGE_DIR}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(docs)
    print(f"Loaded {len(docs)} documents -> {len(chunks)} chunks")

    # Rebuild from scratch for a clean, reproducible store.
    chroma_path = Path(settings.chroma_dir)
    if chroma_path.exists():
        shutil.rmtree(chroma_path)

    print(f"Embedding with {settings.embedding_model} (first run downloads the model)...")
    embeddings = HuggingFaceEmbeddings(model_name=settings.embedding_model)
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name="travel_kb",
        persist_directory=settings.chroma_dir,
    )
    print(f"Vector store written to {settings.chroma_dir}")


if __name__ == "__main__":
    main()
