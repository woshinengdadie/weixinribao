"""硬件码采集模块 - Windows

采集 CPU + 主板 + 硬盘序列号，组合 SHA256
v2.0.1.6: 改用 PowerShell Get-CimInstance 替代 WMIC（Windows 11 22H2+ 已移除 WMIC）
v2.0.1.7: 三项全失败时混入主机名+用户名+C盘卷序列号做兜底，防止不同机器撞码
"""
import hashlib
import os
import platform
import socket
import subprocess
import sys
import logging

logger = logging.getLogger("hardware_id")

# Windows 上隐藏 PowerShell 控制台窗口（避免 PyInstaller --windowed 运行时闪烁）
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run_ps(command: str) -> str:
    """执行 PowerShell 命令并返回首个非空行（清洗标题/BOM/空行）"""
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        # PowerShell 在中文 Windows 上输出 GBK，先试 GBK 再试 UTF-8
        text = None
        for encoding in ("gbk", "utf-8", "utf-16"):
            try:
                text = raw.decode(encoding).strip()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text = raw.decode("utf-8", errors="ignore").strip()

        # 去掉 BOM、首尾空白
        text = text.lstrip("\ufeff").strip()
        # 多行输出取第一个非空行
        for line in text.splitlines():
            line = line.strip().rstrip("\r")
            if line:
                return line
        return ""
    except Exception as e:
        logger.debug("powershell failed for '%s': %s", command[:30], e)
        return ""


def get_cpu_id() -> str:
    """获取 CPU ProcessorId"""
    if platform.system() != "Windows":
        return "NON_WINDOWS_CPU"
    val = _run_ps("(Get-CimInstance Win32_Processor).ProcessorId") or "NO_CPU"
    logger.debug("hardware_id: CPU=%s", val)
    return val


def get_board_sn() -> str:
    """获取主板序列号"""
    if platform.system() != "Windows":
        return "NON_WINDOWS_BOARD"
    val = _run_ps("(Get-CimInstance Win32_BaseBoard).SerialNumber") or "NO_BOARD"
    logger.debug("hardware_id: Board=%s", val)
    return val


def get_disk_sn() -> str:
    """获取第一块硬盘序列号（优先按 Index=0，多盘系统会取主盘）"""
    if platform.system() != "Windows":
        return "NON_WINDOWS_DISK"
    val = _run_ps("(Get-CimInstance Win32_DiskDrive -Filter 'Index=0').SerialNumber") or "NO_DISK"
    logger.debug("hardware_id: Disk=%s", val)
    return val


def _get_fallback_identifier() -> str:
    """硬件全部采集失败时，用机器名+用户名+C盘卷序列号做兜底标识（防止不同机器撞码）"""
    hostname = ""
    username = ""
    c_vol = ""
    try:
        hostname = socket.gethostname() or ""
    except Exception:
        pass
    try:
        username = os.environ.get("USERNAME", "") or os.environ.get("COMPUTERNAME", "")
    except Exception:
        pass
    try:
        c_vol = _run_ps("(Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\").VolumeSerialNumber") or ""
    except Exception:
        pass
    return f"{hostname}|{username}|{c_vol}"


_NO_VALUE = frozenset({"NO_CPU", "NO_BOARD", "NO_DISK", "", "NON_WINDOWS_CPU", "NON_WINDOWS_BOARD", "NON_WINDOWS_DISK"})


def get_hardware_id() -> str:
    """
    生成硬件码：SHA256(CPU_ID | 主板序列号 | 硬盘序列号)
    返回 64 位 hex 字符串。
    若三项全部采集失败，混入机器名+用户名+C盘卷序列号防止不同机器撞码。
    """
    cpu = get_cpu_id()
    board = get_board_sn()
    disk = get_disk_sn()

    parts = [cpu, board, disk]
    all_failed = all(p in _NO_VALUE for p in parts)
    if all_failed:
        fallback = _get_fallback_identifier()
        raw = f"FALLBACK|{fallback}"
        logger.warning("hardware_id: 三项全部采集失败，使用兜底标识: %s", fallback)
    else:
        raw = "|".join(parts)

    result = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    logger.debug("hardware_id: result=%s", result)
    return result


if __name__ == "__main__":
    # 测试：打印本机硬件码
    print("=== 硬件码采集测试 ===")
    print(f"CPU ID:     {get_cpu_id()}")
    print(f"主板序列号:  {get_board_sn()}")
    print(f"硬盘序列号:  {get_disk_sn()}")
    print(f"硬件码:      {get_hardware_id()}")
