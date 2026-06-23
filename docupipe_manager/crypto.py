import binascii

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def encrypt_sm4(plaintext: str, key_hex: str) -> str:
    if len(key_hex) != 32:
        raise ValueError("SM4 key must be 16 bytes (32 hex chars)")
    key = bytes.fromhex(key_hex)
    cipher = Cipher(algorithms.SM4(key), modes.ECB())
    encryptor = cipher.encryptor()
    data = plaintext.encode("utf-8")
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len]) * pad_len
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return binascii.hexlify(ciphertext).decode("ascii")


def decrypt_sm4(ciphertext_hex: str, key_hex: str) -> str:
    if len(key_hex) != 32:
        raise ValueError("SM4 key must be 16 bytes (32 hex chars)")
    key = bytes.fromhex(key_hex)
    cipher = Cipher(algorithms.SM4(key), modes.ECB())
    decryptor = cipher.decryptor()
    ciphertext = bytes.fromhex(ciphertext_hex)
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len].decode("utf-8")
