# logger.py
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import global_queues
from global_queues import LOGGING_QUEUE
from data_models import SensorData

async def file_logging_worker():
    """is_processing_active가 True일 때만 LOGGING_QUEUE의 데이터를 파일에 기록합니다."""
    print("[Logger] 파일 로깅 워커 시작됨.")

    while True:
        data: SensorData = await LOGGING_QUEUE.get()
        try:
            if global_queues.log_file_handler:
                if not global_queues.is_first_entry_in_file:
                    global_queues.log_file_handler.write(",\n")
                global_queues.log_file_handler.write(json.dumps(asdict(data), ensure_ascii=False))
                global_queues.is_first_entry_in_file = False
        finally:
            LOGGING_QUEUE.task_done()

# [추가] 원본 데이터 로깅 파일 초기화 함수
def initialize_raw_log_file(base_filename: str):
    """원본 데이터 로깅을 위한 JSON 파일을 초기화하고 전역 변수를 설정합니다."""
    log_dir = Path("./log")
    log_dir.mkdir(exist_ok=True)
    global_queues.is_first_entry_in_file = True
    global_queues.log_file_path = log_dir / f"{base_filename}_raw.json"
    global_queues.log_file_handler = global_queues.log_file_path.open("w", encoding="utf-8")
    global_queues.log_file_handler.write("[\n")
    print(f"Raw data logging started to: {global_queues.log_file_path}")

# [추가] 원본 데이터 로깅 파일 종료 함수
def finalize_raw_log_file():
    """원본 데이터 JSON 로그 파일을 올바르게 닫고 전역 변수를 초기화합니다."""
    if global_queues.log_file_handler:
        if not global_queues.is_first_entry_in_file:
            global_queues.log_file_handler.seek(global_queues.log_file_handler.tell() - 2)
        global_queues.log_file_handler.write("\n]\n")
        global_queues.log_file_handler.close()
        print(f"Raw data log file saved: {global_queues.log_file_path}")
        # 전역 변수 초기화
        global_queues.log_file_handler = None
        global_queues.log_file_path = None
        global_queues.is_first_entry_in_file = True