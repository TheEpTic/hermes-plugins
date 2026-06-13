"""Encrypted JSON storage using Fernet symmetric encryption.

Provides at-rest encryption for sensitive data files (machine credentials,
session state). Key is derived from a stored file with restricted permissions.

Falls back gracefully: if no key exists, generates one. If decryption fails
(data is plaintext), treats it as unencrypted and re-encrypts on next write.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEY_FILE = ".key"
_RESTRICTED_PERMS = 0o600
_DIR_PERMS = 0o700


class EncryptedStore:
    """Read/write JSON files with Fernet encryption at rest.

    Usage:
        store = EncryptedStore(data_dir=Path("~/.hermes/ssh-tools"))
        data = store.read("machines.json", default={})
        store.write("machines.json", data)
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._fernet: Fernet | None = None

    # ----- Key management -----

    def _ensure_key(self) -> Fernet:
        """Load or generate the encryption key.

        Uses O_EXCL to atomically create the key file — exactly one process
        wins the create, others read the winner's key. Prevents race condition
        where two processes generate different keys and corrupt each other's data.
        """
        if self._fernet is not None:
            return self._fernet

        key_path = self._data_dir / _KEY_FILE

        # Try to read existing key
        if key_path.exists():
            raw = key_path.read_text(encoding="utf-8").strip()
            self._fernet = Fernet(raw.encode())
            return self._fernet

        # Generate new key — atomic create (O_EXCL fails if file already exists)
        key = Fernet.generate_key()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._data_dir, _DIR_PERMS)

        try:
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _RESTRICTED_PERMS)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
        except FileExistsError:
            # Another process won the race — read their key
            raw = key_path.read_text(encoding="utf-8").strip()
            self._fernet = Fernet(raw.encode())
            return self._fernet

        self._fernet = Fernet(key)
        logger.info("Generated new encryption key at %s", key_path)
        return self._fernet

    # ----- Read/Write -----

    def read(self, filename: str, default: Any = None) -> Any:
        """Read and decrypt a JSON file.

        If the file is plaintext (not encrypted), returns it as-is.
        This handles migration from unencrypted data transparently.
        """
        path = self._data_dir / filename
        if not path.exists():
            return default if default is not None else {}

        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return default if default is not None else {}

        # Try encrypted first
        try:
            fernet = self._ensure_key()
            decrypted = fernet.decrypt(raw.encode())
            return json.loads(decrypted)
        except InvalidToken:
            # Not encrypted — treat as plaintext (migration path)
            logger.debug("%s is plaintext, treating as unencrypted", filename)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("Corrupt data in %s: %s", path, exc)
                return default if default is not None else {}
        except Exception as exc:
            logger.warning("Failed to decrypt %s: %s", path, exc)
            return default if default is not None else {}

    def write(self, filename: str, data: Any) -> None:
        """Encrypt and write a JSON file atomically."""
        path = self._data_dir / filename
        fernet = self._ensure_key()

        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._data_dir, _DIR_PERMS)

        plaintext = json.dumps(data, indent=2) + "\n"
        encrypted = fernet.encrypt(plaintext.encode()).decode()

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._data_dir),
            suffix=".tmp",
            prefix=path.stem,
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(path))
            os.chmod(path, _RESTRICTED_PERMS)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def migrate_plaintext(self, filename: str) -> bool:
        """Detect and encrypt a plaintext file.

        Returns True if migration happened, False if already encrypted
        or file doesn't exist.
        """
        path = self._data_dir / filename
        if not path.exists():
            return False

        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return False

        # Check if it's already encrypted by trying to decrypt
        try:
            fernet = self._ensure_key()
            fernet.decrypt(raw.encode())
            return False  # decryption succeeded — already encrypted
        except InvalidToken:
            pass  # not encrypted — proceed with migration
        except Exception:
            return False

        # It's plaintext — read it, encrypt, write back
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Cannot migrate %s: not valid JSON", filename)
            return False

        self.write(filename, data)
        logger.info("Migrated %s from plaintext to encrypted", filename)
        return True
