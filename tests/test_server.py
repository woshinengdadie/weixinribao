"""测试 Flask API 端点（token 鉴权 + 基本 CRUD）"""
import json
import os
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.server import app as _flask_app, _API_TOKEN


@pytest.fixture
def client():
    """Flask 测试客户端"""
    _flask_app.config["TESTING"] = True
    with _flask_app.test_client() as c:
        yield c


def _headers():
    return {"X-Auth-Token": _API_TOKEN, "Content-Type": "application/json"}


# ========== 鉴权测试 ==========

def test_no_token_gets_403(client):
    """无 token 的请求返回 403"""
    r = client.get("/api/config")
    assert r.status_code == 403


def test_wrong_token_gets_403(client):
    r = client.get("/api/config", headers={"X-Auth-Token": "wrong-token"})
    assert r.status_code == 403


def test_valid_token_gets_200(client):
    """有效 token 正常访问"""
    r = client.get("/api/config", headers=_headers())
    assert r.status_code == 200


# ========== 基本 API 端点 ==========

def test_api_config_get(client):
    r = client.get("/api/config", headers=_headers())
    assert r.status_code == 200
    data = r.get_json()
    # 返回格式：{"success": True, "config": {...}} 或直接返回 config
    if "success" in data:
        assert data["success"] is True
    assert data is not None


def test_api_version(client):
    r = client.get("/api/version", headers=_headers())
    assert r.status_code == 200
    data = r.get_json()
    assert "version" in data
    assert data["version"] != "unknown"


def test_api_activate(client):
    r = client.get("/api/activate", headers=_headers())
    assert r.status_code == 200
    data = r.get_json()
    # 激活接口直接返回激活状态对象
    assert data is not None
    assert "activated" in data or "success" in data


def test_api_weekly_list(client):
    r = client.get("/api/weekly/list", headers=_headers())
    assert r.status_code == 200
    data = r.get_json()
    assert data is not None


# ========== 危险端点（应删或保护） ==========

def test_debug_keys_deleted(client):
    """确认 /api/debug/keys 已被删除"""
    r = client.get("/api/debug/keys", headers=_headers())
    assert r.status_code == 404


# ========== /api/file/open 白名单 ==========

def test_file_open_url_ok(client):
    r = client.post("/api/file/open", json={"path": "https://github.com"}, headers=_headers())
    assert r.status_code == 200


def test_file_open_outside_rejected(client):
    r = client.post("/api/file/open", json={"path": "C:\\Windows\\System32\\cmd.exe"}, headers=_headers())
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is False
    assert "允许" in data.get("message", "")


# ========== 配置更新 ==========

def test_config_load(client):
    r = client.get("/api/config", headers=_headers())
    data = r.get_json()
    # 不应有敏感信息泄露（api_key 应脱敏或不暴露）
    if "config" in data:
        raw = str(data["config"])
    else:
        raw = str(data)
    assert "sk-" not in raw  # 原始 API key 不应出现在响应中
