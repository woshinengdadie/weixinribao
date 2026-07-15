"""
待办事项同步模块 - 本地 JSON/HTML 同步
"""
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_output_base_dir() -> str:
    """获取 output 基础目录（兼容开发环境和 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), "output")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def _todo_title(todo: Dict) -> str:
    return todo.get("title", todo.get("task", ""))


def _todo_chat(todo: Dict) -> str:
    return todo.get("chat", todo.get("source", ""))


def _todo_person(todo: Dict) -> str:
    return todo.get("person", todo.get("from", ""))


def _todo_context(todo: Dict) -> str:
    return todo.get("context", todo.get("description", ""))


def save_todos_json(todos: List[Dict], output_dir: str):
    """保存待办事项为 JSON 文件"""
    try:
        os.makedirs(output_dir, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(output_dir, f"todos_{today}.json")
        data = {"date": today, "todos": todos}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"待办JSON已保存: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"保存待办JSON失败: {e}")


def _load_all_todos(base_dir: str) -> Dict[str, List[Dict]]:
    try:
        filepath = os.path.join(base_dir, "all_todos.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_all_todos(all_todos: Dict[str, List[Dict]], base_dir: str):
    """原子写入 all_todos.json（写临时文件 + rename，避免异常中断损坏文件）"""
    try:
        os.makedirs(base_dir, exist_ok=True)
        filepath = os.path.join(base_dir, "all_todos.json")
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(all_todos, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)  # 原子操作
    except Exception as e:
        logger.warning(f"保存 all_todos.json 失败: {e}")


def _merge_todos_today(today_todos: List[Dict], date_str: str, base_dir: str) -> Dict[str, List[Dict]]:
    all_todos = _load_all_todos(base_dir)
    all_todos[date_str] = [
        {
            "title": _todo_title(t),
            "chat": _todo_chat(t),
            "person": _todo_person(t),
            "context": _todo_context(t),
            "priority": t.get("priority", "中"),
        }
        for t in today_todos
    ]
    _save_all_todos(all_todos, base_dir)
    return all_todos


def sync_todos_to_local_html(todos: List[Dict], date_str: str, output_dir: str) -> str:
    """将待办同步到本地 HTML 文件"""
    all_todos = _merge_todos_today(todos, date_str, output_dir)
    html_content = render_todo_html(all_todos, date_str)

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "待办事项.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(f"待办HTML已生成: {filepath}")
    return filepath


def render_todo_html(todos_by_date: Dict[str, List[Dict]], latest_date: str) -> str:
    """渲染全部累计待办为 HTML 页面"""
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>工作待办</title>
<style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; padding: 24px; max-width: 800px; margin: 0 auto; }
    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
    .header h1 { font-size: 24px; font-weight: 700; color: #1a1a1a; }
    .header h1 span { color: #999; font-size: 18px; font-weight: 400; }
    .stats { display: flex; gap: 20px; }
    .stat-item { text-align: center; }
    .stat-num { font-size: 24px; font-weight: 700; color: #1a73e8; }
    .stat-label { font-size: 12px; color: #999; margin-top: 2px; }
    .filter-bar { display: flex; gap: 8px; margin-bottom: 24px; }
    .filter-bar button { padding: 8px 20px; border: 1px solid #ddd; border-radius: 20px; background: white; color: #666; cursor: pointer; font-size: 14px; transition: all 0.2s; }
    .filter-bar button:hover { border-color: #1a73e8; color: #1a73e8; }
    .filter-bar button.active { background: #1a73e8; color: white; border-color: #1a73e8; }
    .date-group { margin-bottom: 20px; }
    .date-header { font-size: 15px; color: #666; margin-bottom: 10px; padding-left: 4px; }
    .date-header .count { color: #999; font-size: 13px; }
    .todo-card { background: white; border-radius: 10px; padding: 16px 20px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); display: flex; align-items: flex-start; gap: 14px; transition: box-shadow 0.2s; cursor: pointer; }
    .todo-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
    .todo-card.completed { opacity: 0.7; }
    .todo-card.completed .todo-title { text-decoration: line-through; color: #999; }
    .todo-card.completed .todo-desc { color: #bbb; }
    .checkbox-wrap { flex-shrink: 0; margin-top: 2px; position: relative; }
    .checkbox-wrap input[type="checkbox"] { display: none; }
    .check-icon { width: 22px; height: 22px; border-radius: 50%; border: 2px solid #d0d0d0; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
    .todo-card.completed .check-icon { background: #27ae60; border-color: #27ae60; }
    .todo-card.completed .check-icon::after { content: '\\2713'; color: white; font-size: 13px; font-weight: 700; }
    .todo-card:hover .check-icon { border-color: #1a73e8; }
    .todo-card.completed:hover .check-icon { border-color: #27ae60; }
    .todo-body { flex: 1; min-width: 0; }
    .todo-title { font-size: 15px; font-weight: 600; color: #1a1a1a; margin-bottom: 6px; line-height: 1.4; }
    .todo-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
    .tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
    .tag-source { background: #e8f0fe; color: #1a73e8; }
    .tag-person { background: #fff3e0; color: #e67e22; }
    .tag-priority-high { background: #fdecea; color: #e74c3c; }
    .tag-priority-mid { background: #fff8e1; color: #f39c12; }
    .tag-priority-low { background: #e8f5e9; color: #27ae60; }
    .todo-desc { font-size: 13px; color: #888; line-height: 1.5; }
    .empty { text-align: center; color: #999; padding: 60px 20px; font-size: 15px; }
    .empty-icon { font-size: 48px; margin-bottom: 12px; }
</style>
</head>
<body>
    <div class="header">
        <h1>工作待办 <span>Todos</span></h1>
        <div class="stats">
            <div class="stat-item"><div class="stat-num" id="statTotal">0</div><div class="stat-label">累计</div></div>
            <div class="stat-item"><div class="stat-num" id="statAll">0</div><div class="stat-label">全部</div></div>
            <div class="stat-item"><div class="stat-num" id="statUncompleted">0</div><div class="stat-label">未完成</div></div>
            <div class="stat-item"><div class="stat-num" id="statCompleted">0</div><div class="stat-label">已完成</div></div>
        </div>
    </div>
    <div class="filter-bar">
        <button class="active" onclick="filterTodos('all', this)">全部</button>
        <button onclick="filterTodos('uncompleted', this)">未完成</button>
        <button onclick="filterTodos('completed', this)">已完成</button>
    </div>
    <div id="todo-list"></div>

<script>
const TODOS_DATA = __DATA_PLACEHOLDER__;

function getCheckedState() {
    try { return JSON.parse(localStorage.getItem('todo_checked') || '{}'); }
    catch(e) { return {}; }
}
function setCheckedState(state) {
    localStorage.setItem('todo_checked', JSON.stringify(state));
}

const WEEKDAY_NAMES = ['周日','周一','周二','周三','周四','周五','周六'];

function formatDate(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d.getTime())) return dateStr;
    return dateStr + '（' + WEEKDAY_NAMES[d.getDay()] + '）';
}

function filterTodos(filter, btn) {
    document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTodos(filter);
}

function updateStats(checkedState) {
    let total = 0, completed = 0;
    for (const date of Object.keys(TODOS_DATA)) {
        const todos = TODOS_DATA[date];
        total += todos.length;
        for (const t of todos) {
            if (checkedState[t.id]) completed++;
        }
    }
    document.getElementById('statTotal').textContent = total;
    document.getElementById('statAll').textContent = total;
    document.getElementById('statCompleted').textContent = completed;
    document.getElementById('statUncompleted').textContent = total - completed;
}

function renderTodos(filter) {
    const container = document.getElementById('todo-list');
    const checkedState = getCheckedState();
    const dates = Object.keys(TODOS_DATA).sort().reverse();

    updateStats(checkedState);

    let html = '';
    for (const date of dates) {
        let todos = TODOS_DATA[date];
        if (filter === 'uncompleted') {
            todos = todos.filter(t => !checkedState[t.id]);
        } else if (filter === 'completed') {
            todos = todos.filter(t => checkedState[t.id]);
        }
        if (todos.length === 0) continue;

        html += '<div class="date-group">';
        html += '<div class="date-header">' + formatDate(date) + ' <span class="count">共' + todos.length + '项</span></div>';

        todos.forEach(todo => {
            const checked = checkedState[todo.id] || false;
            html += '<div class="todo-card' + (checked ? ' completed' : '') + '" onclick="toggleTodo(\\'' + todo.id + '\\')">';
            html += '<div class="checkbox-wrap"><input type="checkbox" ' + (checked ? 'checked' : '') + '><div class="check-icon"></div></div>';
            html += '<div class="todo-body">';
            html += '<div class="todo-title">' + escapeHtml(todo.title) + '</div>';
            html += '<div class="todo-tags">';
            if (todo.chat) html += '<span class="tag tag-source">' + escapeHtml(todo.chat) + '</span>';
            if (todo.person) html += '<span class="tag tag-person">' + escapeHtml(todo.person) + '</span>';
            if (todo.priority) html += '<span class="tag tag-priority-' + todo.priority + '">' + todo.priority + '</span>';
            html += '</div>';
            html += '<div class="todo-desc">' + (todo.desc || '') + '</div>';
            html += '</div></div>';
        });

        html += '</div>';
    }

    if (!html) {
        html = '<div class="empty"><div class="empty-icon">\\u2611</div>暂无待办事项</div>';
    }
    container.innerHTML = html;
}

function toggleTodo(id) {
    const state = getCheckedState();
    state[id] = !state[id];
    setCheckedState(state);

    const activeBtn = document.querySelector('.filter-bar button.active');
    const currentFilter = activeBtn ? (activeBtn.textContent === '未完成' ? 'uncompleted' : activeBtn.textContent === '已完成' ? 'completed' : 'all') : 'all';
    renderTodos(currentFilter);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

renderTodos('all');
</script>
</body>
</html>"""

    data = {}
    for date_str_key, todo_list in todos_by_date.items():
        data[date_str_key] = [
            {
                "id": f"{date_str_key}_{i}",
                "title": _todo_title(t),
                "chat": _todo_chat(t),
                "person": _todo_person(t),
                "priority": t.get("priority", "中"),
            }
            for i, t in enumerate(todo_list)
        ]

    html = html.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False))
    return html
