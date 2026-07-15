"""
密钥验证脚本：判断你的 passphrase 是直接可用的 enc_key 还是需要 PBKDF2 派生

用法:
  python tools/verify_key.py --key <你的passphrase>
  python tools/verify_key.py --key <你的passphrase> --db-dir <数据库目录>
"""
import argparse
import hashlib
import hmac as hmac_mod
import json
import os
import struct

PAGE_SZ = 4096
SALT_SZ = 16
KEY_SZ = 32
RESERVE_SZ = 80  # IV(16) + HMAC(64)
PBKDF2_ITER = 256000


def verify_enc_key(enc_key_bytes: bytes, page1: bytes) -> bool:
    """验证 enc_key 是否能正确解密 page 1"""
    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key_bytes, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + SALT_SZ]
    stored_hmac = page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def main():
    parser = argparse.ArgumentParser(description="微信数据库密钥验证工具")
    parser.add_argument("--key", required=True, help="passphrase 或 enc_key（十六进制字符串）")
    parser.add_argument("--db-dir", default="", help="微信数据库目录（可选，默认从 ~/.wechat-cli/config.json 读取）")
    args = parser.parse_args()

    PASSKEY = args.key

    wechat_cli_dir = os.path.expanduser("~/.wechat-cli")
    config_path = os.path.join(wechat_cli_dir, "config.json")
    db_dir = args.db_dir

    if not db_dir:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                db_dir = json.load(f).get("db_dir", "")

    if not db_dir or not os.path.isdir(db_dir):
        print(f"[X] 数据库目录不存在，请通过 --db-dir 参数指定")
        return

    print(f"[DB] {db_dir}")
    print(f"[KEY] {PASSKEY} ({len(PASSKEY)//2} bytes)")
    print()

    passphrase = bytes.fromhex(PASSKEY)

    db_files = []
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if name.endswith(".db") and not name.endswith("-wal") and not name.endswith("-shm"):
                path = os.path.join(root, name)
                try:
                    sz = os.path.getsize(path)
                    if sz >= PAGE_SZ:
                        db_files.append((os.path.relpath(path, db_dir), path, sz))
                except OSError:
                    pass

    print(f"Found {len(db_files)} .db files")
    if not db_files:
        print("[X] No .db files found")
        return
    print()

    tested = 0
    direct_ok_count = 0
    derived_ok_count = 0

    for rel, path, sz in db_files[:10]:
        try:
            with open(path, "rb") as f:
                page1 = f.read(PAGE_SZ)
        except (OSError, PermissionError) as e:
            print(f"  SKIP {rel} - locked ({e})")
            continue

        if len(page1) < SALT_SZ:
            continue

        tested += 1
        salt = page1[:SALT_SZ]
        salt_hex = salt.hex()
        print(f"-- {rel} ({sz//1024}KB) salt={salt_hex[:16]}...")

        direct_ok = verify_enc_key(passphrase, page1)
        print(f"   direct as enc_key: {'PASS' if direct_ok else 'FAIL'}")

        derived = hashlib.pbkdf2_hmac("sha512", passphrase, salt, PBKDF2_ITER, dklen=KEY_SZ)
        derived_ok = verify_enc_key(derived, page1)
        print(f"   PBKDF2 derived:    {'PASS' if derived_ok else 'FAIL'}")

        if direct_ok:
            direct_ok_count += 1
        if derived_ok:
            derived_ok_count += 1

    print()
    print("=" * 50)
    print(f"Results ({tested} files tested):")
    print(f"  Direct enc_key: {direct_ok_count}/{tested} pass")
    print(f"  PBKDF2 derived:  {derived_ok_count}/{tested} pass")
    print()
    if derived_ok_count > direct_ok_count:
        print(">>> This is a PASSPHRASE, needs PBKDF2 derivation <<<")
    elif direct_ok_count > 0:
        print(">>> This is a direct ENC_KEY, no derivation needed <<<")
    else:
        print(">>> Both failed - key might be wrong <<<")


if __name__ == "__main__":
    main()
