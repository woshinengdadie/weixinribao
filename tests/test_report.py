"""测试日报渲染逻辑"""
import os
import sys
import tempfile
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from report_generator import ReportGenerator


@pytest.fixture
def gen():
    cfg = {"report": {"llm_summary_enabled": True}}
    return ReportGenerator(cfg)


def test_empty_report(gen):
    analysis = {"stat": {"daily_stats": "无数据"}, "date_display": "2026-07-24"}
    summary = {"insights": "", "risk_points": [], "todos": [], "extra": {}}
    with tempfile.TemporaryDirectory() as tmp:
        gen.output_dir = tmp
        gen._gen_summaries_dir = lambda: tmp
        gen._gen_detail_dir = lambda: tmp
        gen._gen_todo_dir = lambda: tmp
        path = gen._write_work_summary(analysis, summary, "2026年7月24日", "2026-07-24")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "工作日报" in content
        assert "2026年7月24日" in content


def test_report_with_extra(gen):
    analysis = {"stat": {"daily_stats": "无数据"}, "date_display": "2026-07-24"}
    summary = {
        "insights": "测试感悟",
        "risk_points": [{"risk": "风险1", "level": "高", "chat": "群", "suggestion": "建议"}],
        "todos": [{"title": "待办1", "chat": "群", "person": "我", "context": "", "priority": "高", "deadline": ""}],
        "extra": {"team_mood": "气氛好", "new_topics": ["需求A", "需求B"]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        gen.output_dir = tmp
        gen._gen_summaries_dir = lambda: tmp
        gen._gen_detail_dir = lambda: tmp
        gen._gen_todo_dir = lambda: tmp
        path = gen._write_work_summary(analysis, summary, "2026年7月24日", "2026-07-24")
        content = open(path, encoding="utf-8").read()
        assert "测试感悟" in content
        assert "风险1" in content
        assert "待办1" in content
        assert "Team Mood" in content or "team_mood" in content
        assert "气氛好" in content
        assert "需求A" in content


def test_report_no_llm_summary(gen):
    gen._config["report"]["llm_summary_enabled"] = False
    analysis = {"stat": {"daily_stats": "无数据"}, "date_display": "2026-07-24"}
    summary = {"insights": "", "risk_points": [], "todos": [{"title": "待办1", "chat": "", "person": "", "context": "", "priority": "中", "deadline": ""}], "extra": {}}
    with tempfile.TemporaryDirectory() as tmp:
        gen.output_dir = tmp
        gen._gen_summaries_dir = lambda: tmp
        gen._gen_detail_dir = lambda: tmp
        gen._gen_todo_dir = lambda: tmp
        path = gen._write_work_summary(analysis, summary, "2026年7月24日", "2026-07-24")
        content = open(path, encoding="utf-8").read()
        # 不带 LLM 总结时 insights 不应出现
        assert "工作感悟" not in content or "### insights" not in content.lower()
