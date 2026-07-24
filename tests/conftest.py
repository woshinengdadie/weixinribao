"""pytest 通用配置"""
import os
import sys
import yaml
import pytest
from datetime import datetime

# 确保项目根在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
if os.path.join(PROJECT_ROOT, "app") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))


@pytest.fixture
def project_root():
    return PROJECT_ROOT


@pytest.fixture
def sample_config():
    return {
        "wechat": {
            "my_name": "测试用户",
            "wxid": "wxid_test123",
            "db_dir": "/tmp/wechat",
            "monitored_chats": ["工作群", "项目群"],
        },
        "llm": {
            "api_key": "sk-test-key",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-3.5-turbo",
            "local_model": {"enabled": False},
        },
        "report": {"llm_summary_enabled": True},
        "rule_prompt": "",
    }


@pytest.fixture
def sample_analysis():
    return {
        "date": "2026-07-24",
        "date_display": "2026年7月24日",
        "participated_chats": {
            "工作群": ["张三: 进度怎么样了？", "我: 还在开发中"],
            "项目群": ["李四: 明天上线", "我: 收到"],
        },
        "attention_messages": [],
        "stat": {
            "topic_words": {},
            "sent_count": 0,
            "word_count": 0,
            "top_senders": {},
            "hot_topics": [],
            "daily_stats": "今日无统计数据",
            "chats_with_new_msgs": 0,
        },
    }
