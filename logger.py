# logger.py
import asyncio
import json
from dataclasses import asdict

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