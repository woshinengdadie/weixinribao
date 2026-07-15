"""Token 本地存储模块"""
import os
import json
from pathlib import Path


def _get_storage_path(app_name: str = "MyApp") -> Path:
    """获取 License 存储路径"""
    # Windows: %APPDATA%\AppName\license.bin
    # Linux/Mac: ~/.config/AppName/license.bin
    if os.name == 'nt':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
    else:
        base = os.path.expanduser('~/.config')

    storage_dir = Path(base) / app_name
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "license.bin"


def save_license(license_token: str, license_id: str, expire_at: str, app_name: str = "MyApp"):
    """保存 License 到本地"""
    path = _get_storage_path(app_name)
    data = {
        "license_token": license_token,
        "license_id": license_id,
        "expire_at": expire_at,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_license(app_name: str = "MyApp") -> dict | None:
    """从本地读取 License，返回 dict 或 None"""
    path = _get_storage_path(app_name)
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def delete_license(app_name: str = "MyApp"):
    """删除本地 License（用于重新激活）"""
    path = _get_storage_path(app_name)
    if path.exists():
        path.unlink()
