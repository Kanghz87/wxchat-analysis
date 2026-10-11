"""启动 HTML 本地网页；原 Streamlit app.py 保留备用。"""
import argparse
from contextlib import closing
from http.client import HTTPConnection, HTTPException
import json
import logging
import socket
import webbrowser

from waitress import create_server

from web.server import create_app


def is_running_analyzer(port: int) -> bool:
    """只识别本机同一工具，避免重复启动或误打开其他服务。"""
    try:
        with closing(HTTPConnection("127.0.0.1", port, timeout=1)) as connection:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            if response.status != 200:
                return False
            data = json.loads(response.read(4096))
            return isinstance(data, dict) and data.get("app") == "wxchat-analysis"
    except (OSError, HTTPException, ValueError):
        return False


def open_browser(url: str) -> None:
    try:
        if webbrowser.open(url, new=2):
            return
    except (OSError, webbrowser.Error):
        pass
    print(f"未能自动打开浏览器，请手动访问：{url}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="微信聊天记录分析 · HTML 本地版")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开本机网页")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间。")
    url = f"http://127.0.0.1:{args.port}"
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            if args.open_browser and is_running_analyzer(args.port):
                print(f"程序已在运行，打开现有网页：{url}", flush=True)
                open_browser(url)
                return
            parser.exit(1, f"端口 {args.port} 已被占用。请先关闭旧版服务，或使用 --port 8502。\n")
    logging.basicConfig()
    server = create_server(create_app(), host="127.0.0.1", port=args.port, threads=4)
    print(f"微信聊天记录分析：{url}", flush=True)
    print("仅在本机运行，请保持此窗口打开，按 Ctrl+C 停止。关闭网页不会停止服务。", flush=True)
    print("正在检查已有分析副本……", flush=True)
    try:
        if args.open_browser:
            open_browser(url)
        server.run()
    finally:
        server.close()


if __name__ == "__main__":
    main()
