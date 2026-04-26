"""
RAG service — semantic search over the FAISS knowledge base.
"""

import os
import json
import numpy as np

# Build path from project root, not from this file's location
# This file is at: app/services/rag_service.py
# Project root is: two levels up
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX_PATH   = os.path.join(PROJECT_ROOT, "knowledge_base", "index", "faiss_index.bin")
CHUNKS_PATH  = os.path.join(PROJECT_ROOT, "knowledge_base", "index", "chunks.json")

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class RAGService:
    def __init__(self):
        self._index  = None
        self._chunks = None
        self._model  = None
        self._ready  = False

    def load(self):
        if self._ready:
            return

        print(f"Looking for FAISS index at: {INDEX_PATH}")

        if not os.path.exists(INDEX_PATH):
            print(f"⚠  FAISS index not found at: {INDEX_PATH}")
            print("   Run: python knowledge_base/build_index.py")
            return

        print("Loading RAG service...")
        import faiss
        from sentence_transformers import SentenceTransformer

        self._index = faiss.read_index(INDEX_PATH)
        with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
            self._chunks = json.load(f)
        self._model = SentenceTransformer(EMBEDDING_MODEL)
        self._ready = True
        print(f"✅ RAG ready — {self._index.ntotal} vectors loaded.")

    def search(self, query: str, top_k: int = 5) -> list:
        if not self._ready:
            return []
        vec = self._model.encode([query], convert_to_numpy=True).astype(np.float32)
        distances, indices = self._index.search(vec, top_k)
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            chunk = self._chunks[idx]
            results.append({
                "text":   chunk["text"],
                "source": chunk["source"],
                "score":  round(float(dist), 4),
            })
        return results

    def build_context(self, query: str, top_k: int = 5) -> str:
        chunks = self.search(query, top_k=top_k)
        if not chunks:
            return "No relevant information found in the knowledge base."
        parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk["source"].replace("_", " ").replace(".txt", "")
            parts.append(f"[Source {i}: {source}]\n{chunk['text']}")
        return "\n\n".join(parts)

    @property
    def is_ready(self) -> bool:
        return self._ready


rag_service = RAGService()
