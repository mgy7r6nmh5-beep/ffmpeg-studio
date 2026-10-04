"""
FFmpeg Studio — 桌面窗口入口

用 pywebview 把现有前端装进一个**原生窗口**（Windows 下走 WebView2），
不再经过浏览器：没有地址栏、没有标签页、没有浏览器 UI。

内部仍然跑一个只绑 127.0.0.1 的 uvicorn（端口自动分配，避免 8787 被占用），
窗口只是它的外壳 —— 所以前端那套 HTML/JS 一行都不用改。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import uvicorn  # noqa: E402
import webview  # noqa: E402

from server.main import app  # noqa: E402

APP_TITLE = "FFmpeg Studio"


def _pick_port() -> int:
    """取一个空闲端口，避免固定端口被占用时启动失败。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _fatal(msg: str) -> None:
    """窗口没起来时用系统弹窗告诉用户，而不是静默退出。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, APP_TITLE, 0x10)
    except Exception:
        pass
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass


class DesktopApi:
    """暴露给前端的原生能力（仅桌面模式）。

    网页模式里没有 ``window.pywebview``，前端会自动回退到 ``<a download>``。

    为什么需要它：WebView2 的下载事件 pywebview 没有接管，桌面版里点
    ``<a download>`` 是**静默失败**的（实测 Downloads 目录前后无变化），
    所以桌面模式必须走原生「另存为」。
    """

    def __init__(self) -> None:
        self.window = None  # 由 main() 注入

    @staticmethod
    def _output_of(job_id: str):
        from server.main import JOBS

        path = (JOBS.get(job_id) or {}).get("outputPath") or ""
        return path if path and os.path.exists(path) else None

    def save_output(self, job_id: str) -> dict:
        """弹原生「另存为」对话框，把处理结果复制到用户选的位置。"""
        src = self._output_of(job_id)
        if not src:
            return {"ok": False, "error": "输出文件不存在或已被移除"}

        picked = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=os.path.expanduser("~"),
            save_filename=os.path.basename(src),
        )
        if not picked:
            return {"ok": False, "cancelled": True}

        dest = picked[0] if isinstance(picked, (list, tuple)) else picked
        try:
            if os.path.abspath(dest) != os.path.abspath(src):
                shutil.copy2(src, dest)
        except Exception as exc:
            return {"ok": False, "error": f"保存失败：{exc}"}
        return {"ok": True, "path": dest}

    def reveal_output(self, job_id: str) -> dict:
        """在资源管理器中选中该输出文件。"""
        src = self._output_of(job_id)
        if not src:
            return {"ok": False, "error": "输出文件不存在或已被移除"}
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(src)])
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}


def main() -> None:
    port = _pick_port()

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_ready(port):
        _fatal(f"后端服务启动失败（端口 {port}）。")
        return

    api = DesktopApi()
    window = webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{port}",
        width=1380,
        height=900,
        min_size=(1080, 700),
        js_api=api,
    )
    api.window = window
    webview.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 无控制台窗口后，异常必须自己弹出来
        import traceback

        _fatal("FFmpeg Studio 启动失败：\n\n" + "".join(
            traceback.format_exception_only(type(exc), exc)))
