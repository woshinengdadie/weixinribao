"""
密钥格式迁移脚本：将旧格式 all_keys.json 转换为 wechat-cli 兼容格式

旧格式:  {rel_path: "hex_key"}
新格式:  {rel_path: {"enc_key": "hex_key", "salt": "hex_salt", "size_mb": N}}

用法: python tools/migrate_keys.py
"""
import json
import os
import sys

PAGE_SZ = 4096
SALT_SZ = 16


def _needs_migration(keys: dict) -> bool:
    """检查是否需要迁移：若任意值是字符串而非字典，则需迁移"""
    if not keys:
        return False
    for v in keys.values():
        if isinstance(v, str):
            return True
    return False


def _read_salt_and_size(db_dir: str, rel_path: str):
    """读取 salt 和文件大小"""
    full_path = os.path.join(db_dir, rel_path)
    try:
        sz = os.path.getsize(full_path)
    except OSError:
        return "", 0.0
    if sz < PAGE_SZ:
        return "", round(sz / 1024 / 1024, 1)
    try:
        with open(full_path, "rb") as f:
            page1 = f.read(PAGE_SZ)
        salt = page1[:SALT_SZ].hex() if len(page1) >= SALT_SZ else ""
    except (OSError, PermissionError):
        salt = ""
    return salt, round(sz / 1024 / 1024, 1)


def migrate_keys():
    wechat_cli_dir = os.path.expanduser("~/.wechat-cli")
    all_keys_path = os.path.join(wechat_cli_dir, "all_keys.json")
    config_path = os.path.join(wechat_cli_dir, "config.json")

    if not os.path.exists(all_keys_path):
        print("[跳过] all_keys.json 不存在，无需迁移")
        return

    # 读取 db_dir
    db_dir = ""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                db_dir = json.load(f).get("db_dir", "")
        except Exception:
            pass

    # 读取旧密钥
    with open(all_keys_path, "r", encoding="utf-8") as f:
        old_keys = json.load(f)

    if not _needs_migration(old_keys):
        print("[跳过] all_keys.json 已是新格式，无需迁移")
        return

    print(f"检测到旧格式，共 {len(old_keys)} 个密钥，开始迁移...")

    # 转换
    new_keys: dict[str, dict] = {}
    errors = 0
    for rel_path, value in old_keys.items():
        if isinstance(value, dict) and "enc_key" in value:
            # 已经是新格式，保留
            new_keys[rel_path] = value
            continue

        enc_key = value if isinstance(value, str) else value.get("enc_key", "")
        salt, size_mb = _read_salt_and_size(db_dir, rel_path) if db_dir else ("", 0.0)
        new_keys[rel_path] = {
            "enc_key": enc_key,
            "salt": salt,
            "size_mb": size_mb,
        }
        if not salt and db_dir:
            errors += 1

    if errors:
        print(f"  注意: {errors} 个数据库无法读取 salt（微信可能正在运行，不影响解密）")

    # 备份旧文件
    backup_path = all_keys_path + ".old_format"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(old_keys, f, indent=2, ensure_ascii=False)
    print(f"  旧格式备份: {backup_path}")

    # 写入新格式
    with open(all_keys_path, "w", encoding="utf-8") as f:
        json.dump(new_keys, f, indent=2, ensure_ascii=False)
    print(f"[完成] all_keys.json 已迁移为新格式 ({len(new_keys)} 个密钥)")

    # 也迁移 accounts 子目录
    accounts_dir = os.path.join(wechat_cli_dir, "accounts")
    if os.path.isdir(accounts_dir):
        for wxid in os.listdir(accounts_dir):
            acc_dir = os.path.join(accounts_dir, wxid)
            if not os.path.isdir(acc_dir):
                continue
            # 迁移 keys.json
            acc_keys = os.path.join(acc_dir, "keys.json")
            if os.path.exists(acc_keys):
                with open(acc_keys, "r", encoding="utf-8") as f:
                    acc_old = json.load(f)
                if _needs_migration(acc_old):
                    acc_new = {}
                    for rp, v in acc_old.items():
                        if isinstance(v, dict) and "enc_key" in v:
                            acc_new[rp] = v
                        else:
                            ek = v if isinstance(v, str) else v.get("enc_key", "")
                            s, sz = _read_salt_and_size(db_dir, rp) if db_dir else ("", 0.0)
                            acc_new[rp] = {"enc_key": ek, "salt": s, "size_mb": sz}
                    with open(acc_keys, "w", encoding="utf-8") as f:
                        json.dump(acc_new, f, indent=2, ensure_ascii=False)
                    print(f"[完成] accounts/{wxid}/keys.json 已迁移")
            # 确保 config.json 存在
            acc_config = os.path.join(acc_dir, "config.json")
            if not os.path.exists(acc_config) and db_dir:
                with open(acc_config, "w", encoding="utf-8") as f:
                    json.dump({"db_dir": db_dir}, f, indent=2)


if __name__ == "__main__":
    try:
        migrate_keys()
        print("\n迁移成功！现在可以重新运行程序了。")
    except Exception as e:
        print(f"\n迁移失败: {e}", file=sys.stderr)
        sys.exit(1)
