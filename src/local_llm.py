"""
本地 LLM 推理引擎
使用 llama-cpp-python 运行 GGUF 格式小模型，无需额外服务。
首次使用：
  1. 自动检测并 pip 安装 llama-cpp-python（Windows 用预编译 wheel，无需 C++ 编译工具）
  2. 从 HuggingFace 下载模型（~380MB），缓存到 models/ 目录
"""

import os
import sys
import json
import time
import logging
import subprocess
import threading
from typing import Dict, List, Optional, Callable

# Windows 上隐藏子进程控制台窗口（避免 PyInstaller --windowed 运行时闪烁）
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

logger = logging.getLogger(__name__)

# llama-cpp-python Windows 预编译 wheel 源（避免用户需要装 C++ 编译工具）
# 如果用户需要 GPU 加速，可自行替换为 cuBLAS/CUDA 版本的 wheel 源
_WHEEL_INDEX_URL = "https://abetlen.github.io/llama-cpp-python/whl/cpu"

# 支持的模型注册表
MODEL_REGISTRY = {
    "qwen2.5-0.5b": {
        "name": "Qwen2.5-0.5B-Instruct (Q4_K_M)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "size_mb": 380,
        "description": "阿里千问0.5B，内存占用低，适合文字总结",
    },
    "qwen2.5-1.5b": {
        "name": "Qwen2.5-1.5B-Instruct (Q4_K_M)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_mb": 985,
        "description": "阿里千问1.5B，效果更好，内存约2GB",
    },
}


def _get_project_root() -> str:
    """获取项目根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _auto_install_llama_cpp(progress_callback: Optional[Callable] = None) -> bool:
    """自动安装 llama-cpp-python（智能检测 CPU 指令集，自动选择预编译或源码编译）

    流程:
      1. 尝试预编译 wheel（快速）
      2. 测试是否能正常加载模型（检测 0xc000001d 非法指令错误）
      3. 如果预编译版本不兼容 CPU，自动从源码编译通用版本

    Returns:
        True 安装成功 / 已安装, False 安装失败
    """
    # 先试试能不能直接导入（可能表示已安装过一轮）
    try:
        import llama_cpp  # noqa: F401
        # 已安装，快速测试是否能正常使用
        if _test_llama_cpp_load():
            return True
        # 加载失败（如 0xc000001d），需要重新编译
        logger.warning("[LocalLLM] 当前安装版本不兼容此 CPU，将重新编译")
        if progress_callback:
            progress_callback("检测到已安装版本不兼容此CPU，将重新从源码编译...")
    except ImportError:
        pass

    if getattr(sys, "frozen", False):
        msg = (
            "本地模型需要 llama-cpp-python 库。"
        )
        logger.error("[LocalLLM] %s", msg)
        if progress_callback:
            progress_callback(f"❌ {msg}")
        return False

    # ---- 第 1 步：尝试预编译 wheel ----
    logger.info("[LocalLLM] 尝试安装预编译版本...")
    if progress_callback:
        progress_callback("正在安装本地模型依赖库...")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "llama-cpp-python",
         "--quiet", "--no-input",
         "--extra-index-url", _WHEEL_INDEX_URL],
        capture_output=True, text=True, timeout=120,
        creationflags=_CREATE_NO_WINDOW,
    )

    if result.returncode == 0:
        # 安装成功，测试是否能正常加载
        if _test_llama_cpp_load():
            logger.info("[LocalLLM] 预编译版本安装成功且兼容")
            if progress_callback:
                progress_callback("✓ 本地模型依赖库安装成功")
            return True
        else:
            # 预编译版本不兼容（CPU 缺 AVX 等指令）
            logger.warning("[LocalLLM] 预编译版本不兼容当前CPU，将从源码编译通用版...")
            if progress_callback:
                progress_callback("预编译版本不兼容，正在从源码编译通用版本（约需3-5分钟）...")

    # ---- 第 2 步：源码编译（禁用 CPU 特定优化，生成通用 x86-64 版本）----
    env = os.environ.copy()
    env["CMAKE_ARGS"] = ("-DLLAMA_NATIVE=OFF -DLLAMA_AVX=OFF "
                         "-DLLAMA_AVX2=OFF -DLLAMA_FMA=OFF -DLLAMA_F16C=OFF")

    result2 = subprocess.run(
        [sys.executable, "-m", "pip", "install", "llama-cpp-python",
         "--quiet", "--no-input", "--no-cache-dir", "--force-reinstall"],
        capture_output=True, text=True, timeout=600,  # 源码编译需要更长时间
        env=env,
        creationflags=_CREATE_NO_WINDOW,
    )

    if result2.returncode == 0:
        logger.info("[LocalLLM] 源码编译安装成功（通用 x86-64）")
        if progress_callback:
            progress_callback("✓ 本地模型依赖库编译安装成功（通用版）")
        return True
    else:
        error_msg = result2.stderr.strip()[-500:] or "未知错误"
        logger.error("[LocalLLM] 源码编译失败: %s", error_msg)
        if progress_callback:
            progress_callback(f"❌ 编译失败。请手动执行:\n"
                              f"  set CMAKE_ARGS=-DLLAMA_NATIVE=OFF ...\n"
                              f"  pip install llama-cpp-python --no-cache-dir")
        return False


def _test_llama_cpp_load() -> bool:
    """快速测试 llama-cpp-python 能否在此 CPU 上正常加载（不加载模型，只导入 DLL）"""
    try:
        from llama_cpp import llama_cpp as _lc_internals
        # 尝试调用底层函数检查 DLL 是否可用
        _ = _lc_internals.llama_supports_mmap()
        return True
    except OSError as e:
        # 0xc000001d = 非法指令（CPU 不支持 AVX 等）
        if hasattr(e, 'winerror') and e.winerror == -1073741795:
            logger.info("[LocalLLM] 检测到此 CPU 不支持当前编译的指令集")
            return False
        raise
    except Exception:
        return False


def _get_project_root() -> str:
    """获取项目根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LocalLLM:
    """本地 LLM 推理引擎（基于 llama-cpp-python）"""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls, config: dict = None) -> Optional["LocalLLM"]:
        """获取全局单例。首次调用时必须传入 config。"""
        if cls._instance is None:
            if config is None:
                return None
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """卸载模型并重置单例（供测试 / 重载配置使用）"""
        with cls._instance_lock:
            if cls._instance:
                cls._instance._unload()
                cls._instance = None

    def __init__(self, config: dict):
        self._local_cfg = config.get("llm", {}).get("local_model", {})
        self._llama = None
        self._model_path = None
        self._load_error = None
        self._inference_lock = threading.Lock()  # 防并发推理导致输出错乱
        self._init_model()

    # ---- 路径 ----

    def _get_project_root(self) -> str:
        """获取项目根目录（兼容 PyInstaller 打包）"""
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _get_model_dir(self) -> str:
        """返回模型保存目录（优先为 exe 同级的 models/，便于用户替换）"""
        path = os.path.join(self._get_project_root(), "models")
        os.makedirs(path, exist_ok=True)
        return path

    def _find_model_file(self) -> Optional[str]:
        """在多个可能位置查找模型文件

        兼容以下打包方式：
          - 开发调试：项目根目录 / models/
          - build_exe.bat / build_app.py：exe 同级 / models/
          - 直接用 PyInstaller：exe 同级 / _internal/models/
        """
        info = self._get_model_info()
        filename = info["filename"]
        root = self._get_project_root()
        candidates = [
            os.path.join(root, "models", filename),
            os.path.join(root, "_internal", "models", filename),
            # 向后兼容：原始目录（项目根目录下的 models/ 在开发环境实际也是这里）
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _ensure_model_dir(self) -> str:
        """确保返回一个可写入的模型目录（用于下载时保存）"""
        return self._get_model_dir()

    def _get_model_info(self) -> dict:
        name = self._local_cfg.get("name", "qwen2.5-0.5b")
        return MODEL_REGISTRY.get(name, MODEL_REGISTRY["qwen2.5-0.5b"])

    # ---- 下载 ----

    def _download_model(self, progress_callback: Optional[Callable] = None) -> str:
        """下载 GGUF 模型文件（如果本地没有的话）"""
        info = self._get_model_info()
        existing = self._find_model_file()

        if existing:
            size_mb = os.path.getsize(existing) / 1024 / 1024
            logger.info("[LocalLLM] 模型已存在: %s", existing)
            if progress_callback:
                progress_callback(f"✓ 本地模型已存在: {info['name']} ({size_mb:.0f}MB)")
            return existing

        model_dir = self._ensure_model_dir()
        model_path = os.path.join(model_dir, info["filename"])

        url = info["url"]
        msg = f"首次使用需下载模型 {info['name']}（{info['size_mb']}MB），请稍候..."
        logger.info("[LocalLLM] 开始下载: %s", url)
        if progress_callback:
            progress_callback(msg)

        try:
            import requests as _req

            resp = _req.get(url, stream=True, timeout=(10, 120))
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            last_pct = 0

            with open(model_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and progress_callback:
                            pct = int(downloaded * 100 / total)
                            if pct - last_pct >= 5:
                                last_pct = pct
                                progress_callback(
                                    f"下载模型 {info['name']}: {pct}% "
                                    f"({downloaded // 1024 // 1024}MB/{total // 1024 // 1024}MB)"
                                )

            logger.info("[LocalLLM] 模型下载完成: %s", model_path)
            if progress_callback:
                progress_callback(f"✓ 模型下载完成 ({info['name']})")
            return model_path

        except Exception as e:
            # 清理失败的下载
            if os.path.exists(model_path):
                try:
                    os.remove(model_path)
                except OSError:
                    pass
            logger.error("[LocalLLM] 模型下载失败: %s", e)
            if progress_callback:
                progress_callback(f"❌ 模型下载失败: {e}")
            raise


    # ---- 初始化 ---- #

    def _init_model(self, progress_callback: Optional[Callable] = None):
        """初始化本地模型（自动安装依赖 + 下载模型 + 加载推理引擎）"""
        # ==== 第 1 步：确保 llama-cpp-python 已安装 ====
        if not _auto_install_llama_cpp(progress_callback):
            self._load_error = (
                "llama-cpp-python 安装失败。请手动执行：\n"
                f"  pip install llama-cpp-python --extra-index-url {_WHEEL_INDEX_URL}\n"
                "安装后重启本程序。"
            )
            return

        # ==== 第 2 步：导入并加载模型 ====
        try:
            from llama_cpp import Llama as _LlamaCls
        except ImportError:
            msg = (
                "llama-cpp-python 导入失败（可能已安装但版本不兼容）。\n"
                "请执行: pip install --upgrade llama-cpp-python"
            )
            logger.error("[LocalLLM] %s", msg)
            self._load_error = msg
            return

        try:
            model_path = self._download_model(progress_callback)
            self._model_path = model_path
            logger.info("[LocalLLM] 使用模型文件: %s", model_path)

            n_ctx = self._local_cfg.get("max_context", 16384)
            n_threads = self._local_cfg.get(
                "threads", max(1, os.cpu_count() or 2)
            )

            if progress_callback:
                progress_callback("正在加载模型到内存（首次加载约30秒）...")

            self._llama = _LlamaCls(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_batch=1024,     # 批量处理加速
                n_ubatch=512,     # 微批次
                n_gpu_layers=0,   # CPU only
                verbose=False,
            )
            logger.info("[LocalLLM] 模型已加载: %s (ctx=%d, threads=%d)",
                        model_path, n_ctx, n_threads)
            if progress_callback:
                progress_callback("✓ 本地模型已就绪")

        except Exception as e:
            logger.error("[LocalLLM] 模型初始化失败: %s", e)
            self._load_error = str(e)
            self._llama = None
            if progress_callback:
                progress_callback(f"❌ 模型加载失败: {e}")

    # ---- 公开接口 ----

    def is_ready(self) -> bool:
        """模型是否已加载就绪"""
        return self._llama is not None

    def get_load_error(self) -> Optional[str]:
        """返回加载错误信息（如果有）"""
        return self._load_error

    def chat(
        self,
        messages: List[Dict],
        temperature: float = 0.5,
        max_tokens: int = 4096,
    ) -> Optional[str]:
        """执行一次对话推理

        Args:
            messages: OpenAI 格式的消息列表
                [{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."}]
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Returns:
            模型回复文本，失败返回 None
        """
        if not self._llama:
            logger.error("[LocalLLM] 模型未加载，无法推理")
            return None

        with self._inference_lock:
            try:
                response = self._llama.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error("[LocalLLM] 推理失败: %s", e)
                return None
        logger.info("[LocalLLM] 推理完成, 生成 %d 字符", len(content or ""))
        return content

    def _unload(self):
        """卸载模型释放内存"""
        self._llama = None
        logger.info("[LocalLLM] 模型已卸载")
