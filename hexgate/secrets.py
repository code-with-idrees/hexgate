"""Encrypted API-key storage.

Keys are sealed with AES-256-GCM using a key derived from the machine
fingerprint (or ``$HEXGATE_MASTER_PASSWORD`` when you want a portable install).
They are never written to ``config.toml`` and never returned by the API —
only a masked preview is exposed.

Threat model: protects against accidental commits, backups and shoulder
surfing. It is not a defence against an attacker who already runs code as you.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import platform
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SALT = b"hexgate::secrets::v1"
AAD = b"hexgate-secrets-v1"
VERSION = 1


class SecretError(RuntimeError):
    """Raised when the secret store cannot be decrypted or parsed."""


def machine_fingerprint() -> str:
    parts = [platform.node(), platform.machine(), str(uuid.getnode())]
    return "|".join(parts)


def _derive(material: str) -> bytes:
    kdf = Scrypt(salt=SALT, length=32, n=2**14, r=8, p=1)
    return kdf.derive(material.encode("utf-8"))


@dataclass(slots=True)
class SecretStore:
    path: Path
    password: str | None = field(default=None, repr=False)
    _cache: dict[str, list[str]] | None = field(default=None, repr=False)
    _mtime: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        if self.password is None:
            self.password = os.environ.get("HEXGATE_MASTER_PASSWORD") or None

    # -- internals -------------------------------------------------------
    def _material(self) -> str:
        return self.password if self.password else machine_fingerprint()

    def _key(self) -> bytes:
        return _derive(self._material())

    # -- reading ---------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, list[str]]:
        """Return ``{provider_id: [key, ...]}``; empty when nothing is stored."""
        if not self.path.exists():
            return {}
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            raise SecretError(f"cannot stat {self.path}: {exc}") from exc
        if self._cache is not None and mtime == self._mtime:
            return {pid: list(keys) for pid, keys in self._cache.items()}
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretError(f"cannot read {self.path}: {exc}") from exc
        if blob.get("v") != VERSION:
            raise SecretError(f"unsupported secrets version in {self.path}")
        try:
            nonce = base64.b64decode(blob["nonce"])
            ciphertext = base64.b64decode(blob["data"])
            plaintext = AESGCM(self._key()).decrypt(nonce, ciphertext, AAD)
            payload = json.loads(plaintext.decode("utf-8"))
        except InvalidTag as exc:
            raise SecretError(
                "Could not decrypt secrets.json. The machine fingerprint changed, or "
                "HEXGATE_MASTER_PASSWORD is different. Run `hexgate keys reset --all` to start over."
            ) from exc
        except (KeyError, ValueError) as exc:
            raise SecretError(f"secrets.json is malformed: {exc}") from exc

        cleaned: dict[str, list[str]] = {}
        for pid, keys in (payload.get("providers") or {}).items():
            if isinstance(keys, list):
                values = [str(k).strip() for k in keys if str(k).strip()]
                if values:
                    cleaned[pid] = values
        self._cache = cleaned
        self._mtime = mtime
        return {pid: list(keys) for pid, keys in cleaned.items()}

    # -- writing ---------------------------------------------------------
    def save(self, providers: dict[str, list[str]]) -> None:
        cleaned = {
            pid: [k.strip() for k in keys if k and k.strip()] for pid, keys in providers.items()
        }
        cleaned = {pid: keys for pid, keys in cleaned.items() if keys}
        payload = json.dumps({"providers": cleaned}, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, payload, AAD)
        blob = {
            "v": VERSION,
            "alg": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "data": base64.b64encode(ciphertext).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        _restrict(tmp)
        tmp.replace(self.path)
        _restrict(self.path)
        self._cache = cleaned
        self._mtime = self.path.stat().st_mtime

    # -- API -------------------------------------------------------------
    def keys(self, provider_id: str, env_names: list[str] | None = None) -> list[str]:
        stored = self.load().get(provider_id, [])
        from_env: list[str] = []
        for name in env_names or []:
            value = os.environ.get(name, "").strip()
            if value:
                from_env.append(value)
        # Env keys come first so a shell override wins for local debugging.
        merged: list[str] = []
        for key in [*from_env, *stored]:
            if key and key not in merged:
                merged.append(key)
        return merged

    def set_keys(self, provider_id: str, keys: list[str]) -> None:
        data = self.load()
        cleaned = [k.strip() for k in keys if k and k.strip()]
        if cleaned:
            data[provider_id] = cleaned
        else:
            data.pop(provider_id, None)
        self.save(data)

    def remove(self, provider_id: str) -> None:
        data = self.load()
        if data.pop(provider_id, None) is not None:
            self.save(data)

    def masked(self, provider_id: str, env_names: list[str] | None = None) -> list[str]:
        return [mask_key(k) for k in self.keys(provider_id, env_names)]

    def provider_ids(self) -> list[str]:
        return sorted(self.load())


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "•" * max(len(key) - 2, 1)
    return f"{key[:6]}…{key[-4:]}"


def _restrict(path: Path) -> None:
    with contextlib.suppress(OSError):  # pragma: no cover
        path.chmod(0o600)
