import os
import requests
import numpy as np
from qdrant_client import QdrantClient


HF_EMBEDDING_API = os.getenv("HF_EMBEDDING_API")

#codeembedder as seperate endpoint 
class CodeEmbedder:

    def __init__(self):
        if not HF_EMBEDDING_API:
            raise ValueError("HF_EMBEDDING_API environment variable not set")

    def embed(self, text: str) -> np.ndarray:

        response = requests.post(
            HF_EMBEDDING_API,
            json={
                "text": text
            },
            timeout=120
        )

        response.raise_for_status()

        embedding = response.json()["embedding"]

        return np.array([embedding], dtype=np.float32)


class QdrantRetriever:

    def __init__(
        self,
        qdrant_url: str,
        qdrant_api_key: str,
        collection_name: str,
        top_k: int = 5
    ):

        self.client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key,
            timeout=300
        )

        self.collection_name = collection_name
        self.top_k = top_k

    def retrieve(self, query_embedding: np.ndarray) -> list:

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_embedding.flatten().tolist(),
            limit=self.top_k
        ).points

        retrieved = []

        for result in results:

            payload = result.payload or {}

            retrieved.append({
                "summary": payload.get("document", ""),
                "metadata": payload
            })

        return retrieved