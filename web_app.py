"""启动 HTML 本地网页；原 Streamlit app.py 保留备用。"""
import argparse
import socket

from waitress import serve

from web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="微信聊天记录分析 · HTML 本地版")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间。")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.exit(1, f"端口 {args.port} 已被占用。请先关闭旧版服务，或使用 --port 8502。\n")
    print(f"微信聊天记录分析：http://127.0.0.1:{args.port}", flush=True)
    print("仅在本机运行，按 Ctrl+C 停止。正在检查已有分析副本……", flush=True)
    serve(create_app(), host="127.0.0.1", port=args.port, threads=4)


if __name__ == "__main__":
    main()
