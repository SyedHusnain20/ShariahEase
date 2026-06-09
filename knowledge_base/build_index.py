"""
FAISS index builder for ShariahEase knowledge base.
Uses all-MiniLM-L6-v2 (full quality, 90MB).
Shows live progress so VS Code does not appear frozen.

Run once before starting the app:
    python knowledge_base/build_index.py
"""

import os
import json
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR    = os.path.join(BASE_DIR, "docs")
INDEX_DIR   = os.path.join(BASE_DIR, "index")
INDEX_PATH  = os.path.join(INDEX_DIR, "faiss_index.bin")
CHUNKS_PATH = os.path.join(INDEX_DIR, "chunks.json")

EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # full quality model


def load_documents():
    docs = []
    for filename in os.listdir(DOCS_DIR):
        if filename.endswith(".txt"):
            path = os.path.join(DOCS_DIR, filename)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            docs.append({"filename": filename, "content": content})
            print(f"  Loaded: {filename}")
    return docs


def chunk_document(content, filename):
    paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 60]
    return [{"text": p, "source": filename, "chunk_id": i} for i, p in enumerate(paragraphs)]


def build_index():
    os.makedirs(INDEX_DIR, exist_ok=True)

    print("\n── Step 1: Loading documents ──────────────────────")
    documents = load_documents()
    if not documents:
        print("ERROR: No .txt files in knowledge_base/docs/")
        sys.exit(1)

    print("\n── Step 2: Chunking documents ─────────────────────")
    all_chunks = []
    for doc in documents:
        chunks = chunk_document(doc["content"], doc["filename"])
        all_chunks.extend(chunks)
        print(f"  {doc['filename']}: {len(chunks)} chunks")
    total = len(all_chunks)
    print(f"  Total chunks to embed: {total}")

    print("\n── Step 3: Loading embedding model ────────────────")
    print(f"  Model : {EMBEDDING_MODEL}")
    print(f"  Size  : ~90MB (downloads once, cached after)")
    print(f"  Note  : This may take 1-2 min on first run. It is NOT frozen.")
    t0 = time.time()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)
    print(f"  Model loaded in {time.time()-t0:.1f}s")

    print(f"\n── Step 4: Embedding {total} chunks ───────────────")
    print(f"  Processing in batches of 4 — progress shown below:")
    print(f"  (Estimated time: {total // 4 * 3}–{total // 4 * 5} seconds on your CPU)\n")

    texts          = [c["text"] for c in all_chunks]
    all_embeddings = []
    BATCH          = 4   # tiny batches = frequent progress updates

    for i in range(0, total, BATCH):
        batch     = texts[i : i + BATCH]
        t_batch   = time.time()
        vecs      = model.encode(batch, convert_to_numpy=True, show_progress_bar=False)
        elapsed   = time.time() - t_batch
        done      = min(i + BATCH, total)
        pct       = int(done / total * 100)
        bar       = ("█" * (pct // 5)).ljust(20)
        all_embeddings.append(vecs)
        print(f"  [{bar}] {pct:3d}%  chunk {done:3d}/{total}  ({elapsed:.1f}s/batch)")
        sys.stdout.flush()   # force VS Code terminal to print immediately

    embeddings = np.vstack(all_embeddings).astype(np.float32)
    print(f"\n  Embedding complete. Shape: {embeddings.shape}")

    print("\n── Step 5: Building FAISS index ───────────────────")
    import faiss
    dim   = embeddings.shape[1]
    index = faiss.IndexFlatL2(dim)
    index.add(embeddings)
    print(f"  {index.ntotal} vectors, dimension {dim}")

    print("\n── Step 6: Saving to disk ─────────────────────────")
    faiss.write_index(index, INDEX_PATH)
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    print(f"  Index  saved → {INDEX_PATH}")
    print(f"  Chunks saved → {CHUNKS_PATH}")

    print(f"\n✅ Knowledge base ready — {index.ntotal} vectors.\n")


if __name__ == "__main__":
    build_index()
