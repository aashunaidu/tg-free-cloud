FERNET_AVAILABLE = False
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    import base64
    FERNET_AVAILABLE = True
except Exception:
    FERNET_AVAILABLE = False

def derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000, backend=default_backend())
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return key

def maybe_encrypt_bytes(data: bytes, passphrase: str, salt: bytes) -> bytes | None:
    if not FERNET_AVAILABLE or not passphrase:
        return None
    key = derive_fernet_key(passphrase, salt)
    f = Fernet(key)
    return f.encrypt(data)
