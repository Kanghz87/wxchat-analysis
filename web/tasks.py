"""一个后台任务；不引入任务队列或外部服务。"""
from copy import deepcopy
from threading import Lock, Thread
from uuid import uuid4

from core.errors import UserError
from core.logging_utils import log_failure


def error_message(operation: str, error: Exception) -> str:
    try:
        log_failure(operation, error)
    except (OSError, UserError):
        pass
    if isinstance(error, UserError):
        return str(error)
    if isinstance(error, PermissionError):
        return f"{operation}失败：权限不足或文件被占用，请检查目录和微信运行权限。"
    if isinstance(error, OSError):
        return f"{operation}失败：文件无法读写，请检查路径、磁盘空间及文件占用。"
    return f"{operation}失败，详细调用栈已写入 workspace/logs/app.log。"


class BusyError(UserError):
    pass


class Tasks:
    def __init__(self):
        self.lock = Lock()
        self.current: dict | None = None

    def snapshot(self) -> dict | None:
        with self.lock:
            return deepcopy(self.current)

    @property
    def busy(self) -> bool:
        current = self.snapshot()
        return bool(current and current["state"] == "running")

    def submit(self, action: str, operation) -> dict:
        with self.lock:
            if self.current and self.current["state"] == "running":
                raise BusyError("已有任务正在运行，请等待完成。")
            self.current = {"id": uuid4().hex, "action": action, "state": "running", "messages": [], "error": ""}
            initial = deepcopy(self.current)

        def progress(message: str) -> None:
            with self.lock:
                self.current["messages"].append(message)
                self.current["messages"] = self.current["messages"][-100:]

        def run() -> None:
            try:
                operation(progress)
            except Exception as error:
                message = error_message("本地数据库任务", error)
                with self.lock:
                    self.current.update(state="failed", error=message)
            else:
                with self.lock:
                    self.current["state"] = "complete"

        Thread(target=run, name="wechat-workspace", daemon=True).start()
        return initial
