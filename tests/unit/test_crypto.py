import pytest

from docupipe_manager.crypto import decrypt_sm4, encrypt_sm4

KEY_HEX = "0123456789abcdef0123456789abcdef"


def test_encrypt_decrypt_roundtrip():
    plaintext = "hello world"
    cipher = encrypt_sm4(plaintext, KEY_HEX)
    assert cipher != plaintext
    decrypted = decrypt_sm4(cipher, KEY_HEX)
    assert decrypted == plaintext


def test_encrypt_decrypt_unicode():
    plaintext = "中文测试 🔐"
    cipher = encrypt_sm4(plaintext, KEY_HEX)
    decrypted = decrypt_sm4(cipher, KEY_HEX)
    assert decrypted == plaintext


def test_encrypt_decrypt_empty():
    plaintext = ""
    cipher = encrypt_sm4(plaintext, KEY_HEX)
    decrypted = decrypt_sm4(cipher, KEY_HEX)
    assert decrypted == plaintext


def test_encrypt_decrypt_long_text():
    plaintext = "A" * 10000
    cipher = encrypt_sm4(plaintext, KEY_HEX)
    decrypted = decrypt_sm4(cipher, KEY_HEX)
    assert decrypted == plaintext


def test_invalid_key_length():
    with pytest.raises(ValueError, match="SM4 key must be 16 bytes"):
        encrypt_sm4("test", "invalid-key")


def test_invalid_key_length_decrypt():
    with pytest.raises(ValueError, match="SM4 key must be 16 bytes"):
        decrypt_sm4("aabb", "invalid-key")


def test_decrypt_corrupted_ciphertext():
    with pytest.raises(Exception):
        decrypt_sm4("not-hex", KEY_HEX)


def test_different_keys_produce_different_output():
    key1 = "0123456789abcdef0123456789abcdef"
    key2 = "fedcba9876543210fedcba9876543210"
    plaintext = "hello"
    c1 = encrypt_sm4(plaintext, key1)
    c2 = encrypt_sm4(plaintext, key2)
    assert c1 != c2


def test_wrong_key_produces_garbage():
    key1 = "0123456789abcdef0123456789abcdef"
    key2 = "fedcba9876543210fedcba9876543210"
    cipher = encrypt_sm4("secret data", key1)
    result = decrypt_sm4(cipher, key2)
    assert result != "secret data"


def test_cbc_generates_different_ciphertext():
    plaintext = "same data"
    c1 = encrypt_sm4(plaintext, KEY_HEX)
    c2 = encrypt_sm4(plaintext, KEY_HEX)
    assert c1 != c2  # 随机 IV 保证每次密文不同


def test_legacy_ecb_still_decrypts():
    """向后兼容：旧 ECB 格式的密文仍然能解密。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex(KEY_HEX)
    cipher = Cipher(algorithms.SM4(key), modes.ECB())
    encryptor = cipher.encryptor()
    data = b"hello world! good"
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len]) * pad_len
    legacy_ciphertext = (encryptor.update(padded) + encryptor.finalize()).hex()
    assert len(legacy_ciphertext) % 32 == 0
    decrypted = decrypt_sm4(legacy_ciphertext, KEY_HEX)
    assert decrypted == "hello world! good"
