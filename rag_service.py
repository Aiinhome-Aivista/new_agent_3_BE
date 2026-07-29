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

import re

def extract_day_key(s):
    if not s:
        return ""
    s_lower = str(s).strip().lower()
    m = re.search(r'day\s*\d+', s_lower)
    if m:
        return m.group(0).replace(" ", "")  # e.g. "day1", "day2"
    return s_lower.split(':')[0].strip()

def query_knowledge(query_text: str, plan_id: int = None, kt_day: str = None, n_results: int = 5) -> list[dict]:
    formatted_results = []
    
    if plan_id is not None:
        try:
            p_id = int(plan_id)
        except (ValueError, TypeError):
            p_id = plan_id

        # Get all chunks for this plan_id from Chroma DB
        try:
            all_res = collection.get(where={"plan_id": p_id})
        except Exception:
            all_res = None
        
        if all_res and all_res.get("documents") and len(all_res["documents"]) > 0:
            docs_list = all_res["documents"]
            metas_list = all_res.get("metadatas", [{}] * len(docs_list))
            
            if kt_day:
                # Filter strictly by matching day key (e.g. "day1", "day2")
                target_day_key = extract_day_key(kt_day)
                day_matched_chunks = []
                
                for doc, meta in zip(docs_list, metas_list):
                    chunk_day = meta.get("day") or meta.get("kt_day") or ""
                    chunk_day_key = extract_day_key(chunk_day)
                    
                    if target_day_key and chunk_day_key and (target_day_key == chunk_day_key or target_day_key in chunk_day_key or chunk_day_key in target_day_key):
                        day_matched_chunks.append(doc)
                
                if day_matched_chunks:
                    try:
                        results = collection.query(query_texts=[query_text], where={"plan_id": p_id}, n_results=n_results * 2)
                        if results and results.get("documents") and len(results["documents"]) > 0:
                            q_docs = results["documents"][0]
                            q_metas = results.get("metadatas", [[{}] * len(q_docs)])[0]
                            q_dists = results.get("distances", [[0] * len(q_docs)])[0]
                            
                            for doc, meta, dist in zip(q_docs, q_metas, q_dists):
                                chunk_day = meta.get("day") or meta.get("kt_day") or ""
                                chunk_day_key = extract_day_key(chunk_day)
                                if target_day_key and chunk_day_key and (target_day_key == chunk_day_key or target_day_key in chunk_day_key or chunk_day_key in target_day_key):
                                    if doc not in [r['text'] for r in formatted_results]:
                                        formatted_results.append({"text": doc, "distance": dist})
                    except Exception:
                        pass
                    
                    # If similarity search filtered out chunks, use matched chunks directly
                    if not formatted_results:
                        for doc in day_matched_chunks[:n_results]:
                            formatted_results.append({"text": doc, "distance": 0.0})
                            
                    return formatted_results
                else:
                    # STRICT RULE: No documents uploaded for this specific day!
                    return []
            else:
                # Final Assessment: return across all chunks for this plan
                try:
                    results = collection.query(query_texts=[query_text], where={"plan_id": p_id}, n_results=n_results)
                    if results and results.get("documents") and len(results["documents"]) > 0:
                        q_docs = results["documents"][0]
                        q_dists = results.get("distances", [[0] * len(q_docs)])[0]
                        for doc, dist in zip(q_docs, q_dists):
                            formatted_results.append({"text": doc, "distance": dist})
                        return formatted_results
                except Exception:
                    pass
                
                for doc in docs_list[:n_results]:
                    formatted_results.append({"text": doc, "distance": 0.0})
                return formatted_results

    return formatted_results
