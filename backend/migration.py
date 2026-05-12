import chromadb
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
from tqdm import tqdm

CHROMA_PATH = "./chroma_store"
CHROMA_COLLECTION = "codebert_python"

QDRANT_COLLECTION = "codeassist"

VECTOR_SIZE = 768  # CodeBERT embedding dimension
BATCH_SIZE = 50

print("Connecting to ChromaDB...")

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
chroma_collection = chroma_client.get_collection(CHROMA_COLLECTION)

# Get total count
total_vectors = chroma_collection.count()

print(f"Found {total_vectors} vectors in ChromaDB")

print("Connecting to Qdrant...")

qdrant_client = QdrantClient(
    url="https://1817678d-d1ec-41a4-af92-1f260fca2718.us-east4-0.gcp.cloud.qdrant.io", 
    api_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6YWE2MzA3NjAtNjZlMi00MDRjLTg0YmUtY2I1NjE4YWEwYTQ5In0.LwNu8GTrH7d8UhSCAwyjEKKh-o2WmJ9VXs4F_AMmFBs",
    timeout=300
)

existing_collections = [
    col.name for col in qdrant_client.get_collections().collections
]

if QDRANT_COLLECTION not in existing_collections:
    print(f"Creating Qdrant collection: {QDRANT_COLLECTION}")

    qdrant_client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE
        )
    )

    print("Collection created")
else:
    print("Collection already exists")


print("Starting migration...\n")

for offset in tqdm(range(0, total_vectors, BATCH_SIZE), desc="Migrating"):

    # Fetch batch from Chroma
    data = chroma_collection.get(
        limit=BATCH_SIZE,
        offset=offset,
        include=["embeddings", "metadatas", "documents"]
    )

    ids = data["ids"]
    embeddings = data["embeddings"]
    metadatas = data["metadatas"]
    documents = data["documents"]


    # Convert to Qdrant points
    points = []

    for i in range(len(ids)):

        payload = {}

        # Add metadata if exists
        if metadatas[i]:
            payload.update(metadatas[i])

        # Add document separately
        payload["document"] = documents[i]

        # Preserve original Chroma ID
        payload["original_id"] = ids[i]

        points.append(
            PointStruct(
                id=offset + i,
                vector=embeddings[i],
                payload=payload
            )
        )

    # Upload batch to Qdrant
    qdrant_client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points
    )

print("\nMigration completed successfully!")
print(f"Transferred {total_vectors} vectors to Qdrant.")