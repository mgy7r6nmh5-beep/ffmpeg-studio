"""
FFmpeg Studio — 桌面窗口入口

用 pywebview 把现有前端装进一个**原生窗口**（Windows 下走 WebView2），
不再经过浏览器：没有地址栏、没有标签页、没有浏览器 UI。

内部仍然跑一个只绑 127.0.0.1 的 uvicorn（端口自动分配，避免 8787 被占用），
窗口只是它的外壳 —— 所以前端那套 HTML/JS 一行都不用改。
"""
from __future__ import annotations

import os
import socket
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


def main() -> None:
    port = _pick_port()

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_ready(port):
        _fatal(f"后端服务启动失败（端口 {port}）。")
        return

    webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{port}",
        width=1380,
        height=900,
        min_size=(1080, 700),
    )
    webview.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 无控制台窗口后，异常必须自己弹出来
        import traceback

        _fatal("FFmpeg Studio 启动失败：\n\n" + "".join(
            traceback.format_exception_only(type(exc), exc)))
