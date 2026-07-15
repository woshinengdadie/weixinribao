"""离线验签模块 - 用内置 RSA 公钥验证 License Token"""
import base64
import json
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature
from license_client.exceptions import (
    SignatureInvalidError, ExpiredError, HardwareMismatchError, NotActivatedError
)
from license_client.hardware_id import get_hardware_id


# ========== RSA 公钥（硬编码+文件双保险） ==========
# 优先尝试从同目录 public_key.pem 加载，失败则使用内置硬编码版本
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzNZQawkfalWyIrqDvo2g
E07kTQn2gt9zXgu9gyJzIinu4EODzaFymPOaeKHvL2L/QLD3voeot6o7SONPIdmg
UQoNXQ8H9fpiBRZ0R1wdyNKXT7dHsUce48DhcmuocZ0KCGZ0dfgiOAZe4XLmjqP4
GZirl8pB5EPoJ07OUaegP8dKH3kS7LvR3jwb2qYjHOeOWzHNN9fYFrd3v4bivdRB
+GIbVBRT9ukWjS1KfyHYAfKN7nuiLB8Y3DZJ5XY4wlonJRt0Cf+OvAJvrV9SFYd8
yV+L/oJywflk4sEu8CRSiqXCWwT2cLZyfsusECQ62jX8tAzz0lHUqFPZ8k9c5YVh
UwIDAQAB
-----END PUBLIC KEY-----"""


_public_key = None


def _load_public_key():
    global _public_key
    if _public_key is None:
        # 尝试从文件加载，失败则用内置的
        import os
        key_path = os.path.join(os.path.dirname(__file__), "public_key.pem")
        if os.path.exists(key_path):
            with open(key_path, 'rb') as f:
                _public_key = serialization.load_pem_public_key(f.read())
        else:
            if b"REPLACE_WITH" in PUBLIC_KEY_PEM:
                raise RuntimeError(
                    "公钥未配置！请将服务器的 public.pem 复制到 "
                    "client/license_client/public_key.pem，或替换 verifier.py 中的 PUBLIC_KEY_PEM"
                )
            _public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
    return _public_key


def verify_token(token: str, product_id: str) -> dict:
    """
    验证 License Token
    返回 payload dict
    异常: NotActivatedError, SignatureInvalidError, HardwareMismatchError, ExpiredError
    """
    if not token:
        raise NotActivatedError("未激活，请先输入激活码")

    # 1. 分离 payload 和签名
    try:
        parts = token.split('.')
        if len(parts) != 2:
            raise SignatureInvalidError("Token 格式错误")
        payload_b64, sig_b64 = parts
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        signature = base64.urlsafe_b64decode(sig_b64)
    except Exception:
        raise SignatureInvalidError("Token 解析失败")

    # 2. RSA 公钥验签（核心防伪造）
    public_key = _load_public_key()
    try:
        public_key.verify(signature, payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise SignatureInvalidError("签名验证失败，Token 可能被篡改")

    # 3. 解析 payload
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise SignatureInvalidError("Payload 解析失败")

    # 4. 硬件码匹配检查（防迁移）
    local_hw_id = get_hardware_id()
    if payload.get("hw_id") != local_hw_id:
        raise HardwareMismatchError(
            "机器码已变更，需重新激活（可能原因：硬件更换、系统更新、软件升级）。"
            "请使用 get_hwid.bat 获取最新机器码，联系管理员获取新激活码。"
        )

    # 5. 有效期检查
    expire_at = payload.get("expire_at", 0)
    if int(time.time()) > expire_at:
        raise ExpiredError(f"License 已过期（到期时间: {time.strftime('%Y-%m-%d', time.localtime(expire_at))}）")

    # 6. 产品标识检查
    if payload.get("product_id") != product_id:
        raise SignatureInvalidError("产品标识不匹配")

    return payload
