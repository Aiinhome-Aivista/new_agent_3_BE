import chromadb
import uuid
import os

db_path = os.path.join(os.path.dirname(__file__), 'chroma_db')
client = chromadb.PersistentClient(path=db_path)
collection = client.get_or_create_collection(name="kt_knowledge")

def _chunk_text(text, chunk_size=500, overlap=50):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks

def add_document(doc_id: str, text: str, metadata: dict) -> int:
    chunks = _chunk_text(text)
    if not chunks:
        return 0
        
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metadatas = [{**metadata, "chunk_index": i} for i in range(len(chunks))]
    
    collection.add(
        documents=chunks,
        metadatas=metadatas,
        ids=ids
    )
    
    return len(chunks)

def query_knowledge(query_text: str, plan_id: int = None, n_results: int = 5) -> list[dict]:
    kwargs = {
        "query_texts": [query_text],
        "n_results": n_results
    }
    
    if plan_id is not None:
        try:
            kwargs["where"] = {"plan_id": int(plan_id)}
        except (ValueError, TypeError):
            kwargs["where"] = {"plan_id": plan_id}
        
    results = collection.query(**kwargs)
    
    formatted_results = []
    if results and results.get("documents") and len(results["documents"]) > 0:
        docs = results["documents"][0]
        distances = results.get("distances", [[0] * len(docs)])[0]
        
        for doc, distance in zip(docs, distances):
            formatted_results.append({
                "text": doc,
                "distance": distance
            })
            
    return formatted_results
