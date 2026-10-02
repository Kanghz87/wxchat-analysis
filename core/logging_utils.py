import logging
from pathlib import Path
import traceback

from core.errors import UserError
from core.paths import WORKSPACE_ROOT
from core.workspace import owned


def log_failure(operation: str, error: Exception) -> None:
    """保留异常类型和完整调用栈；不记录局部变量及可能含正文/密钥的异常值。"""
    logger = logging.getLogger("wechat_analyzer")
    if not logger.handlers:
        path: Path = owned(WORKSPACE_ROOT / "logs" / "app.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    detail = str(error) if isinstance(error, UserError) else type(error).__name__
    logger.error("%s: %s\n%s", operation, detail, "".join(traceback.format_tb(error.__traceback__)))
