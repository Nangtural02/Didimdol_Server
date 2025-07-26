# csv_logger.py
import asyncio
import csv
from dataclasses import asdict

import global_queues
from data_models import InferenceResult

async def csv_result_logger():
    """
    [모델 테스트 모드용] RESULT_QUEUE에서 결과를 꺼내 CSV 파일에 저장합니다.
    """
    print("[CSV Logger] CSV 결과 로거 시작됨.")
    while True:
        result: InferenceResult = await global_queues.RESULT_QUEUE.get()
        try:
            if global_queues.is_processing_active and global_queues.csv_writer:
                # dataclass를 딕셔너리로 변환하여 CSV에 쓰기
                global_queues.csv_writer.writerow(asdict(result))
                # 파일에 즉시 반영되도록 flush
                global_queues.csv_file_handler.flush()
                print(f"[CSV Logger] {result.count}번째 결과 CSV에 저장 완료.")
                # ✅ 2. [핵심] 최신 결과를 전역 캐시에 저장
                global_queues.last_inference_result = result

                # ✅ 3. [핵심] 대기 중인 핸들러에게 "새 결과 준비 완료" 신호를 보냄
                global_queues.NEW_RESULT_EVENT.set()
        finally:
            global_queues.RESULT_QUEUE.task_done()