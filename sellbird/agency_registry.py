"""Optional agency-wide sent-recipient deduplication for separate local homes.

All participating installations must use the same local registry path and
secret, with a distinct workspace identifier. Only HMAC fingerprints are
stored; no customer's contact record is copied into the shared file.
"""

import asyncio
import hashlib
import hmac
import os
import sqlite3
from pathlib import Path


class AgencyRegistry:
    def __init__(self, path: str, key: str, workspace: str):
        if len(key) < 32 or not workspace.strip():
            raise ValueError("Agency registry needs a 32-character secret and unique workspace name.")
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise ValueError("Agency registry path must be absolute.")
        self.key = key.encode("utf-8")
        self.workspace = workspace.strip()

    def _hash(self, value: str) -> str:
        return hmac.new(self.key, value.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        db.execute("CREATE TABLE IF NOT EXISTS recipients (fingerprint TEXT PRIMARY KEY, "
                   "workspace TEXT NOT NULL, reserved_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        return db

    def _check(self, address: str, reserve: bool) -> bool:
        """True if available to this workspace; reserve atomically if asked."""
        db = self._connect()
        try:
            if reserve:
                db.execute("BEGIN IMMEDIATE")
            fingerprint = self._hash(address)
            row = db.execute("SELECT workspace FROM recipients WHERE fingerprint = ?",
                             (fingerprint,)).fetchone()
            allowed = row is None or row[0] == self.workspace
            if allowed and reserve and row is None:
                db.execute("INSERT INTO recipients (fingerprint, workspace) VALUES (?, ?)",
                           (fingerprint, self.workspace))
            if reserve:
                db.commit()
            return allowed
        finally:
            db.close()

    async def available(self, address: str) -> bool:
        return await asyncio.to_thread(self._check, address, False)

    async def reserve(self, address: str) -> bool:
        return await asyncio.to_thread(self._check, address, True)


def configured_registry() -> AgencyRegistry | None:
    path = os.environ.get("SELLBIRD_AGENCY_REGISTRY_PATH", "").strip()
    if not path:
        return None
    return AgencyRegistry(path, os.environ.get("SELLBIRD_AGENCY_REGISTRY_KEY", ""),
                          os.environ.get("SELLBIRD_AGENCY_WORKSPACE", ""))
