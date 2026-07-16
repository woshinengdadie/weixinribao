"""
PyInstaller hook for wechat_cli package.
Ensures all submodules and binary dependencies are collected.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# Collect all submodules, binaries, and data files
datas, binaries, hiddenimports = collect_all("wechat_cli")

# Explicitly collect all submodules (recursive)
hiddenimports += collect_submodules("wechat_cli")

# Additional hidden imports that wechat_cli depends on at runtime
hiddenimports += [
    "wechat_cli.main",
    "wechat_cli.__main__",
    "wechat_cli.commands",
    "wechat_cli.commands.init",
    "wechat_cli.commands.sessions",
    "wechat_cli.commands.history",
    "wechat_cli.commands.search",
    "wechat_cli.commands.contacts",
    "wechat_cli.commands.new_messages",
    "wechat_cli.commands.members",
    "wechat_cli.commands.export",
    "wechat_cli.commands.stats",
    "wechat_cli.commands.unread",
    "wechat_cli.commands.favorites",
    "wechat_cli.commands.export_html",
    "wechat_cli.commands.export_all_html",
    "wechat_cli.commands.export_all_accounts",
    "wechat_cli.core",
    "wechat_cli.core.context",
    "wechat_cli.core.crypto",
    "wechat_cli.core.db_cache",
    "wechat_cli.core.key_utils",
    "wechat_cli.core.messages",
    "wechat_cli.keys",
    "wechat_cli.keys.common",
    "wechat_cli.keys.scanner_windows",
    "wechat_cli.output",
    "click",
    "pymem",
    "psutil",
]
