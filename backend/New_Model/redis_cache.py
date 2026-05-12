import json
import hashlib
import redis
from typing import Optional
from datetime import timedelta


class RedisCache:
    """
    Function-level cache using Redis.
    Key: codeassist:{md5(filepath::function_name)}
    Value: { ast_hash, code, dependencies, analysis_status, llm_result }
    TTL: 24 hours default
    """

    def __init__(self, host="localhost", port=6379, password=None, db=0, ttl_hours=24, ssl=False):
        self.client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            ssl=ssl,
            decode_responses=True
        )
        self.ttl = timedelta(hours=ttl_hours)
        self._available = self._check_connection()

    def _check_connection(self) -> bool:
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _make_key(self, file_path: str, function_name: str) -> str:
        raw = f"{file_path}::{function_name}"
        return f"codeassist:{hashlib.md5(raw.encode()).hexdigest()}"

    def get(self, file_path: str, function_name: str) -> Optional[dict]:
        if not self._available:
            return None
        try:
            key = self._make_key(file_path, function_name)
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    def set(self, file_path: str, function_name: str, payload: dict) -> bool:
        if not self._available:
            return False
        try:
            key = self._make_key(file_path, function_name)
            self.client.setex(key, self.ttl, json.dumps(payload))
            return True
        except Exception:
            return False

    def invalidate(self, file_path: str, function_name: str) -> bool:
        if not self._available:
            return False
        try:
            key = self._make_key(file_path, function_name)
            self.client.delete(key)
            return True
        except Exception:
            return False

    def is_stale(self, file_path: str, function_name: str, current_ast_hash: str) -> bool:
        cached = self.get(file_path, function_name)
        if not cached:
            return True
        return cached.get("ast_hash") != current_ast_hash

    def get_stats(self) -> dict:
        if not self._available:
            return {"status": "unavailable - running without cache"}
        try:
            info = self.client.info()
            keys = self.client.keys("codeassist:*")
            return {
                "status": "connected",
                "cached_nodes": len(keys),
                "memory_used": info.get("used_memory_human", "unknown")
            }
        except Exception:
            return {"status": "error"}
