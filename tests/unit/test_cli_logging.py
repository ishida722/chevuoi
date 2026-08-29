import logging
import re
from unittest.mock import patch

from chevuoi.interfaces.cli import main as cli_main
from chevuoi.interfaces.cli.main import LOG_FORMAT, setup_file_logging

# asctime の既定書式（例: 2026-08-29 10:12:34,567）
ASCTIME_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ")


def test_setup_file_logging_writes_timestamped_lines(tmp_path):
    log_file = tmp_path / "logs" / "vuoi.log"
    setup_file_logging(log_file)
    try:
        logger = logging.getLogger("chevuoi.test")
        logger.setLevel(logging.INFO)
        logger.info("処理開始: card-1")
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(log_file):
                root.removeHandler(handler)
                handler.close()
    content = log_file.read_text(encoding="utf-8")
    assert "処理開始: card-1" in content
    # 行頭に日時（asctime）が付いていること
    assert ASCTIME_PREFIX.match(content)


def test_log_format_has_timestamp():
    record = logging.LogRecord("chevuoi.test", logging.INFO, "", 0, "処理開始", None, None)
    line = logging.Formatter(LOG_FORMAT).format(record)
    assert ASCTIME_PREFIX.match(line)
    assert line.endswith("INFO chevuoi.test: 処理開始")


def test_main_configures_stderr_handler_with_timestamp(tmp_path):
    """main() が標準エラー向けハンドラに日時付き書式を設定していること。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    for handler in saved:
        root.removeHandler(handler)
    try:
        with (
            patch.object(cli_main, "load_config"),
            patch.object(cli_main, "setup_file_logging"),
            patch.object(cli_main, "AppModule"),
            patch.object(cli_main, "Injector"),
            patch("builtins.print"),
        ):
            cli_main.main(["workflow", "list"])
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert stream_handlers, "basicConfig で StreamHandler が登録されていること"
        assert stream_handlers[0].formatter._fmt == LOG_FORMAT
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved:
            root.addHandler(handler)
