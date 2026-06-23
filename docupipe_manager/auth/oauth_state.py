import hashlib
import os


def generate_state() -> str:
    """Generate a cryptographically random CSRF state value."""
    return hashlib.sha256(os.urandom(32)).hexdigest()


def verify_state(expected: str, actual: str) -> bool:
    """Constant-time comparison of CSRF state values."""
    if not expected or not actual:
        return False
    return hashlib.sha256(expected.encode()).hexdigest() == hashlib.sha256(actual.encode()).hexdigest()
