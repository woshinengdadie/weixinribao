"""
调度器 - 后台线程，按定时设置自动运行日报生成
支持暂停/恢复/立即触发（源自 PySide6 版 TaskScheduler 的成熟设计）
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class DailyScheduler:
    """每日定时调度器（统一版）

    特性：
    - 定时运行（每天固定时间）和间隔运行（每N分钟）
    - Event-based 安全停止（比标志位更可靠）
    - 暂停/恢复
    - 立即触发一次（非阻塞）
    - 动态配置：每次循环通过 config_provider 获取最新配置
    """

    def __init__(self, config_provider: Callable, run_callback: Callable):
        """
        Args:
            config_provider: 返回当前配置字典的回调（每次循环动态获取）
            run_callback: 执行任务的回调
        """
        self._config_provider = config_provider
        self._run_callback = run_callback
        self._stop_event = threading.Event()
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._last_run_time: Optional[datetime] = None
        self._next_run_time: Optional[datetime] = None

    def start(self):
        """启动调度器"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._update_next_run()
        logger.info(f"调度器已启动，下次运行: {self._next_run_time}")

    def stop(self):
        """停止调度器并等待线程退出（最多 3 秒），防止快速重启时创建重复线程"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                logger.warning("调度器线程 3 秒内未退出，可能仍在运行")
        self._thread = None
        logger.info("调度器已停止")

    def pause(self):
        """暂停调度（不停止线程，只是跳过执行）"""
        self._paused = True
        logger.info("调度器已暂停")

    def resume(self):
        """恢复调度"""
        self._paused = False
        logger.info("调度器已恢复")

    def trigger_now(self):
        """立即触发一次任务（非阻塞）"""
        t = threading.Thread(target=self._run_callback, daemon=True)
        t.start()
        logger.info("调度器立即触发一次任务")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def last_run_time(self) -> Optional[datetime]:
        return self._last_run_time

    @property
    def next_run(self) -> Optional[str]:
        return self._next_run_time.strftime("%Y-%m-%d %H:%M") if self._next_run_time else None

    def _update_next_run(self):
        """计算下次运行时间"""
        config = self._config_provider()
        sched = config.get("schedule", {})
        mode = sched.get("mode", "scheduled")
        now = datetime.now()

        if mode == "interval":
            interval = sched.get("interval_seconds", 1800)
            if self._last_run_time:
                self._next_run_time = self._last_run_time + timedelta(seconds=interval)
            else:
                self._next_run_time = now + timedelta(seconds=interval)
        else:
            time_str = sched.get("time", "17:30")
            try:
                hour, minute = map(int, time_str.split(":"))
                next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_dt <= now:
                    next_dt += timedelta(days=1)
                self._next_run_time = next_dt
            except (ValueError, TypeError):
                self._next_run_time = None

    def _loop(self):
        """调度器主循环"""
        logger.info("调度器主循环开始")

        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(1)
                continue

            config = self._config_provider()
            sched = config.get("schedule", {})
            mode = sched.get("mode", "scheduled")
            now = datetime.now()

            should_run = False

            if mode == "interval":
                interval = sched.get("interval_seconds", 1800)
                if self._last_run_time is None or (now - self._last_run_time).total_seconds() >= interval:
                    should_run = True
            else:
                time_str = sched.get("time", "17:30")
                daily_once = sched.get("daily_once", True)
                try:
                    hour, minute = map(int, time_str.split(":"))
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    if daily_once:
                        # 每天只跑一次，在设定时间后的 5 分钟内触发
                        if self._last_run_time is None or self._last_run_time.date() < now.date():
                            if target <= now <= target + timedelta(minutes=5):
                                should_run = True
                    else:
                        # 每次到点都跑
                        time_diff = (now - target).total_seconds()
                        if 0 <= time_diff < 60 and (
                            self._last_run_time is None
                            or (now - self._last_run_time).total_seconds() > 3600
                        ):
                            should_run = True
                except (ValueError, TypeError):
                    should_run = False

            if should_run:
                self._last_run_time = now
                self._update_next_run()
                logger.info(f"调度器触发任务: {now.isoformat()}")
                try:
                    self._run_callback()
                except Exception as e:
                    logger.error(f"调度执行失败: {e}")

            # 更新下次运行时间（配置可能动态变化）
            if self._next_run_time is None:
                self._update_next_run()

            time.sleep(15)  # 每15秒检查一次

        logger.info("调度器主循环结束")
