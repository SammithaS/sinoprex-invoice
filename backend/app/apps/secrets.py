"""Encryption for app secrets (API keys, app passwords, webhook URLs, tokens).

- Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256, random IV, timestamped) before
  they reach the database. The key lives only in the server environment (`SECRETS_KEY`),
  never in the database or the repository, so a database leak alone reveals nothing.
- Rotation: `SECRETS_KEY` may hold several comma-separated keys. The first one encrypts; all
  of them decrypt (MultiFernet), so a new key can be added in front and old values re-encrypted
  with `rotate()` without anyone re-entering a secret.
- The API never returns plaintext: only whether a secret is set, a 4-character hint and when
  it was last changed. Plaintext exists only in memory while an app runs, and `scrub()` keeps
  it out of logs and error messages.
"""

from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretsUnavailableError(RuntimeError):
    """No SECRETS_KEY configured (or an invalid one): secrets can't be stored or read."""


class SecretBox:
    def __init__(self, keys: Iterable[str]) -> None:
        parsed = [k.strip() for k in keys if k.strip()]
        if not parsed:
            raise SecretsUnavailableError(
                "SECRETS_KEY is not set: generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                "print(Fernet.generate_key().decode())\"` and add it to the server's settings"
            )
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in parsed])
        except (ValueError, TypeError) as exc:
            raise SecretsUnavailableError(f"SECRETS_KEY is not a valid Fernet key: {exc}") from exc

    @classmethod
    def from_setting(cls, value: str) -> "SecretBox":
        return cls(value.split(","))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise SecretsUnavailableError(
                "a stored secret can't be decrypted with the current SECRETS_KEY "
                "(was the key changed without keeping the old one?)"
            ) from exc

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt with the first (newest) key."""
        return self._fernet.rotate(ciphertext.encode()).decode()


def hint(value: str) -> str:
    """What the UI may show of a secret: its last 4 characters (none if it's very short)."""
    return value[-4:] if len(value) >= 12 else ""


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Remove secret values (and their URL-encoded forms) from a log line or error."""
    from urllib.parse import quote

    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(s, "••••").replace(quote(s, safe=""), "••••")
    return text
