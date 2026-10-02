from functools import lru_cache
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request
from plotly.offline import get_plotlyjs
from werkzeug.exceptions import HTTPException

from core.errors import UserError
from core.paths import discover_data_path
from web.service import Service
from web.tasks import BusyError, error_message


@lru_cache(maxsize=1)
def plotly_source() -> str:
    return get_plotlyjs()


def create_app(service: Service | None = None, initialize: bool = True) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    service = service or Service()
    app.extensions["wechat_service"] = service

    @app.before_request
    def local_only():
        if urlsplit("http://" + request.host).hostname not in ("127.0.0.1", "localhost"):
            return jsonify(error="仅允许从本机访问。"), 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="不允许跨来源访问本机数据。"), 403
        if request.method in ("POST", "PUT") and not request.is_json:
            return jsonify(error="请求格式必须为 JSON。"), 415

    @app.after_request
    def no_cache(response):
        if request.path == "/" or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def body() -> dict:
        data = request.get_json()
        if not isinstance(data, dict):
            raise UserError("请求参数无效。")
        return data

    @app.errorhandler(Exception)
    def handle_error(error):
        if isinstance(error, HTTPException):
            return jsonify(error="请求地址或格式无效。"), error.code
        message = error_message("网页操作", error)
        return jsonify(error=message), 409 if isinstance(error, BusyError) else 400 if isinstance(error, UserError) else 500

    @app.get("/")
    def index():
        preferences = service.preferences.read()
        theme = preferences.get("theme", "light") if preferences["remember"]["theme"] else "light"
        return render_template("index.html", theme=theme, preferences=preferences)

    @app.get("/assets/plotly.min.js")
    def plotly_js():
        response = Response(plotly_source(), mimetype="text/javascript")
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    @app.get("/api/status")
    def status():
        return jsonify(service.status())

    @app.post("/api/workspace/inspect")
    def inspect_path():
        source = discover_data_path(body().get("source", ""))
        return jsonify(source=str(source.root), databases=[path.name for path in source.databases],
                       files=[path.name for path in source.files()])

    @app.post("/api/tasks")
    def task():
        return jsonify(service.submit(body())), 202

    @app.get("/api/tasks/<task_id>")
    def task_status(task_id):
        current = service.tasks.snapshot()
        if current is None or current["id"] != task_id:
            return jsonify(error="此任务已结束或服务已重新启动，请查看当前工作区状态。"), 404
        return jsonify(current)

    @app.get("/api/contacts")
    def contacts():
        return jsonify(contacts=service.contacts())

    @app.post("/api/analysis")
    def analysis():
        return jsonify(service.analyze(body()))

    @app.get("/api/preferences")
    def preferences():
        return jsonify(service.preferences.read())

    @app.put("/api/preferences")
    def save_preferences():
        return jsonify(service.preferences.save(body(), service.active_info.get("account", "")))

    if initialize:
        service.startup()
    return app
