# pyright: reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false
"""
PBKDF2 密钥派生脚本
将 wx_key 提取的 passphrase 转换为每个数据库的实际加密密钥

WeChat 4.1+ 不再缓存派生密钥，只存储 passphrase
需要用 PBKDF2-HMAC-SHA512(passphrase, salt, 256000, dklen=32) 派生

用法:
  python derive_keys.py --db-dir <路径> --passphrase <hex> --wxid <wxid>
  或从 config.yaml 读取默认值:
  python derive_keys.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


def _load_defaults_from_config() -> dict[str, str]:
    """从 config/config.yaml 读取默认配置"""
    defaults: dict[str, str] = {}
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "config", "config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            wechat_cfg = cfg.get("wechat", {})
            if wechat_cfg.get("db_dir"):
                defaults["db_dir"] = str(wechat_cfg["db_dir"])
            if wechat_cfg.get("wxid"):
                defaults["wxid"] = str(wechat_cfg["wxid"])
    except ImportError:
        pass
    return defaults


def derive_keys(db_dir: str, passphrase_hex: str, wxid: str) -> str:
    """执行密钥派生（兼容 wechat-cli 的 all_keys.json 格式）- 多线程加速"""
    from concurrent.futures import ThreadPoolExecutor

    passphrase = bytes.fromhex(passphrase_hex)

    if not os.path.isdir(db_dir):
        print(f"\u274c 数据库目录不存在: {db_dir}")
        sys.exit(1)

    # 只收集核心消息数据库
    TARGET_PREFIXES = ("MSG", "MicroMsg", "ChatMsg", "Misc", "Emotion")
    file_list: list[str] = []
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if name.endswith(".db") and not name.endswith("-wal") and not name.endswith("-shm"):
                if name.startswith(TARGET_PREFIXES):
                    path = os.path.join(root, name)
                    try:
                        if os.path.getsize(path) >= 4096:
                            file_list.append(path)
                    except OSError:
                        continue

    total = len(file_list)
    if total == 0:
        print("未找到可处理的数据库文件")
        return ""

    print(f"发现 {total} 个数据库文件，{os.cpu_count() or 4} 线程并行派生...")

    result: dict[str, dict[str, str | float]] = {}

    def _derive_one(path: str) -> tuple[str, dict[str, str | float]] | None:
        try:
            with open(path, "rb") as f:
                salt = f.read(16)
            enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)
            rel = os.path.relpath(path, db_dir)
            return rel, {
                "enc_key": enc_key.hex(),
                "salt": salt.hex(),
                "size_mb": round(os.path.getsize(path) / 1024 / 1024, 1),
            }
        except OSError:
            return None

    workers = min(os.cpu_count() or 4, total)
    success = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item in pool.map(_derive_one, file_list):
            if item:
                rel, info = item
                result[rel] = info
                success += 1
                if success % 5 == 0:
                    print(f"  进度: {success}/{total}")

    # 写入 ~/.wechat-cli/
    wechat_cli_dir = os.path.expanduser("~/.wechat-cli")
    os.makedirs(wechat_cli_dir, exist_ok=True)

    keys_path = os.path.join(wechat_cli_dir, "all_keys.json")
    with open(keys_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # 兼容 accounts 子目录（wechat-cli 多账号模式使用 keys.json + config.json）
    account_dir = os.path.join(wechat_cli_dir, "accounts", wxid)
    os.makedirs(account_dir, exist_ok=True)
    with open(os.path.join(account_dir, "keys.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(os.path.join(account_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"db_dir": db_dir}, f, indent=2)

    config_path_out = os.path.join(wechat_cli_dir, "config.json")
    with open(config_path_out, "w", encoding="utf-8") as f:
        json.dump({"db_dir": db_dir}, f, indent=2)

    print(f"\n密钥派生完成! {success}/{total} 成功, 保存至: {keys_path}")
    return keys_path


def main() -> None:
    defaults = _load_defaults_from_config()

    parser = argparse.ArgumentParser(
        description="PBKDF2 密钥派生 — 将 WeChat passphrase 转换为数据库加密密钥"
    )
    _ = parser.add_argument(
        "--db-dir",
        default=defaults.get("db_dir", ""),
        help="微信数据库目录路径（默认从 config.yaml 读取）",
    )
    _ = parser.add_argument(
        "--passphrase",
        default="",
        help="passphrase 的十六进制字符串（必填，不会从配置文件读取）",
    )
    _ = parser.add_argument(
        "--wxid",
        default=defaults.get("wxid", ""),
        help="微信 wxid（默认从 config.yaml 读取）",
    )

    args = parser.parse_args()

    if not args.db_dir:
        parser.error("--db-dir 必须指定（或在 config.yaml 的 wechat.db_dir 中配置）")
    if not args.passphrase:
        parser.error("--passphrase 必须指定（出于安全考虑，不存储在配置文件中）")
    if not args.wxid:
        parser.error("--wxid 必须指定（或在 config.yaml 的 wechat.wxid 中配置）")

    _ = derive_keys(args.db_dir, args.passphrase, args.wxid)


if __name__ == "__main__":
    main()
