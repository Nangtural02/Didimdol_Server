# logger.py
import asyncio
import json
from dataclasses import asdict

from global_queues import (
    LOGGING_QUEUE, is_processing_active, log_file_handler, is_first_entry_in_file
)
from data_models import SensorData

async def file_logging_worker():
    """is_processing_active가 True일 때만 LOGGING_QUEUE의 데이터를 파일에 기록합니다."""
    print("[Logger] 파일 로깅 워커 시작됨.")
    global is_first_entry_in_file
    while True:
        data: SensorData = await LOGGING_QUEUE.get()
        try:
            # ✅ is_processing_active 상태를 한번 더 확인 (안전장치)
            if is_processing_active and log_file_handler:
                if not is_first_entry_in_file:
                    log_file_handler.write(",\n")
                log_file_handler.write(json.dumps(asdict(data), ensure_ascii=False))
                is_first_entry_in_file = False
        finally:
            LOGGING_QUEUE.task_done()