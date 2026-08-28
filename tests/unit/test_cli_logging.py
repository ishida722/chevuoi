import logging

from chevuoi.interfaces.cli.main import setup_file_logging


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
    assert content[:4].isdigit()
