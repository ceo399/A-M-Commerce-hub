"""ログ設定。

構造化（キー=値）に近い簡潔なフォーマットで標準出力へ。
本番では集約基盤（CloudWatch等）が拾いやすいよう1行1イベントにする。
"""
from __future__ import annotations

import logging

from app.config import settings

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # アクセスログは冗長になりやすいので既定を抑制
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
