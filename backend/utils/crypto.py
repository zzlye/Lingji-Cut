# backend/utils/crypto.py
# 加密工具 - 使用 AES 加密存储 API 密钥

import os
import base64
from cryptography.fernet import Fernet

# 密钥文件路径
KEY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
KEY_PATH = os.path.join(KEY_DIR, ".secret.key")


def _get_or_create_key() -> bytes:
    """获取或创建加密密钥"""
    os.makedirs(KEY_DIR, exist_ok=True)
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_PATH, "wb") as f:
            f.write(key)
        return key


def encrypt_api_key(plain_key: str) -> str:
    """加密 API 密钥，返回 base64 编码的加密字符串"""
    key = _get_or_create_key()
    f = Fernet(key)
    encrypted = f.encrypt(plain_key.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    """解密 API 密钥，返回原始字符串"""
    key = _get_or_create_key()
    f = Fernet(key)
    decrypted = f.decrypt(base64.b64decode(encrypted_key))
    return decrypted.decode("utf-8")
