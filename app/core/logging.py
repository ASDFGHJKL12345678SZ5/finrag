"""结构化日志（JSON 行）。

和 DataCrew 一样的选择：日志即数据。评测跑批时按 request_id 关联全链路，
排障不用翻文本。这里保持轻量——标准库 logging + JSON formatter，
不引第三方依赖（FinRAG 的依赖哲学：能标准库不引包）。
"""
import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # extra={"context": {...}} 的约定与 DataCrew 一致（团队协作时格式统一）
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
