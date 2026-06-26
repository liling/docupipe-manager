import binascii
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_CBC_FLAG_LEN = 2  # hex chars for version byte (0x02 → "02")
_IV_HEX_LEN = 32   # 16 bytes IV as hex


def encrypt_sm4(plaintext: str, key_hex: str) -> str:
    if len(key_hex) != 32:
        raise ValueError("SM4 key must be 16 bytes (32 hex chars)")
    key = bytes.fromhex(key_hex)
    iv = secrets.token_bytes(16)
    cipher = Cipher(algorithms.SM4(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    data = plaintext.encode("utf-8")
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len]) * pad_len
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return binascii.hexlify(b"\x02" + iv + ciphertext).decode("ascii")


def decrypt_sm4(ciphertext_hex: str, key_hex: str) -> str:
    if len(key_hex) != 32:
        raise ValueError("SM4 key must be 16 bytes (32 hex chars)")
    key = bytes.fromhex(key_hex)

    # CBC format: "02" + IV(32 hex) + ciphertext(N*32 hex) → total hex % 32 == 2
    # ECB legacy: ciphertext(N*32 hex) → total hex % 32 == 0
    if len(ciphertext_hex) % 32 == _CBC_FLAG_LEN:
        raw = bytes.fromhex(ciphertext_hex)
        iv = raw[_CBC_FLAG_LEN // 2 : _CBC_FLAG_LEN // 2 + 16]
        ciphertext = raw[_CBC_FLAG_LEN // 2 + 16 :]
        cipher = Cipher(algorithms.SM4(key), modes.CBC(iv))
    else:
        ciphertext = bytes.fromhex(ciphertext_hex)
        cipher = Cipher(algorithms.SM4(key), modes.ECB())

    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len].decode("utf-8")
