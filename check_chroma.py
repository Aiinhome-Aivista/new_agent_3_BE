import chromadb

# ChromaDB path (Normally ./chroma_db)
client = chromadb.PersistentClient(path="./chroma_db") 

# List all collections present in ChromaDB
collections = client.list_collections()

print(f"Total Collections Found: {len(collections)}")

for col in collections:
    print(f"\n================ Collection Name: {col.name} ================")
    
    # Fetch collection object
    collection = client.get_collection(name=col.name)
    results = collection.get(include=["metadatas"])
    
    total_docs = len(results['ids'])
    print(f"Total Stored Chunks/Documents: {total_docs}\n")
    
    for idx, meta in enumerate(results['metadatas']):
        print(f"--- Chunk {idx + 1} ---")
        print(f"  Plan ID  : {meta.get('plan_id')}")
        print(f"  Day      : {meta.get('day')}")
        print(f"  Manager  : {meta.get('manager_id')}")
        print(f"  File Name: {meta.get('file_name')}")
        print(f"  Created  : {meta.get('created_at')}")