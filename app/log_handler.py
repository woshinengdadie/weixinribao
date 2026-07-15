"""
日志捕获模块 - 将运行日志推送到 SSE 前端
"""
import queue
import logging
from datetime import datetime


class LogQueue:
    """全局日志队列，SSE 端点从此读取"""
    _instance = None

    def __init__(self):
        self.queue = queue.Queue(maxsize=500)

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def write(self, msg: str):
        """写入一条日志（按行拆分）"""
        for line in msg.split("\n"):
            if line.strip():
                try:
                    self.queue.put_nowait({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "text": line.strip(),
                    })
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                        self.queue.put_nowait({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "text": line.strip(),
                        })
                    except queue.Empty:
                        pass

    def read_all(self):
        """读取当前队列中所有消息（非阻塞）"""
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return items


class LogQueueHandler(logging.Handler):
    """将 Python logging 消息转发到 LogQueue（前端 SSE 日志面板）"""

    def __init__(self, level=logging.INFO):
        super().__init__(level)
        self._queue = LogQueue.get()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._queue.write(msg)
        except Exception:
            self.handleError(record)

    @classmethod
    def attach(cls, target_logger: str = "",
               level: int = logging.INFO,
               fmt: str = "[%(levelname)s] %(name)s: %(message)s"):
        """将 LogQueueHandler 附加到指定 logger（或根 logger）

        Args:
            target_logger: logger 名称，空字符串=根 logger
            level: 最小日志级别
            fmt: 日志格式（UI 中显示的格式）
        """
        handler = cls(level)
        handler.setFormatter(logging.Formatter(fmt))
        logging.getLogger(target_logger).addHandler(handler)
        return handler

    @classmethod
    def detach_all(cls):
        """从根 logger 移除所有 LogQueueHandler"""
        for h in list(logging.getLogger().handlers):
            if isinstance(h, cls):
                logging.getLogger().removeHandler(h)


class ProgressWriter:
    """模拟文件对象，捕获 print() 输出到日志队列"""

    def __init__(self, log_queue: LogQueue = None):
        self.log_queue = log_queue or LogQueue.get()
        self.buffer = ""

    def write(self, text: str):
        self.buffer += text
        if "\n" in self.buffer:
            lines = self.buffer.split("\n")
            for line in lines[:-1]:
                if line.strip():
                    self.log_queue.write(line)
            self.buffer = lines[-1]

    def flush(self):
        if self.buffer.strip():
            self.log_queue.write(self.buffer)
            self.buffer = ""

    def info(self, msg: str):
        self.log_queue.write(msg)

    def success(self, msg: str):
        self.log_queue.write(msg)

    def warn(self, msg: str):
        self.log_queue.write(msg)

    def error(self, msg: str):
        self.log_queue.write(msg)
