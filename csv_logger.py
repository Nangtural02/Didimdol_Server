# csv_logger.py
import asyncio
import csv
from dataclasses import asdict
from pathlib import Path

import global_queues
from data_models import InferenceResult

async def csv_result_logger():
    """
    [모델 테스트 모드용] RESULT_QUEUE에서 결과를 꺼내 CSV 파일에 저장합니다.
    """
    print("[CSV Logger] CSV 결과 로거 시작됨.")
    while True:
        result = await global_queues.RESULT_QUEUE.get()

        if result is None:
            print("[CSV Logger] 종료 신호 수신. 로거를 종료합니다.")
            global_queues.RESULT_QUEUE.task_done()
            break # 무한 루프 탈출

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

# [추가] CSV 결과 파일 초기화 함수
def initialize_csv_log_file(base_filename: str):
    """결과 저장을 위한 CSV 파일을 초기화하고 전역 변수를 설정합니다."""
    log_dir = Path("./log")
    log_dir.mkdir(exist_ok=True)
    global_queues.csv_file_path = log_dir / f"{base_filename}_results.csv"
    global_queues.csv_file_handler = global_queues.csv_file_path.open("w", encoding="utf-8", newline="")
    fieldnames = [field.name for field in InferenceResult.__dataclass_fields__.values()]
    global_queues.csv_writer = csv.DictWriter(global_queues.csv_file_handler, fieldnames=fieldnames)
    global_queues.csv_writer.writeheader()
    print(f"Model test results will be saved to: {global_queues.csv_file_path}")

# [추가] CSV 결과 파일 종료 함수
def finalize_csv_log_file():
    """결과 CSV 파일을 닫고 전역 변수를 초기화합니다."""
    if global_queues.csv_file_handler:
        global_queues.csv_file_handler.close()
        print(f"CSV log saved to: {global_queues.csv_file_path}")
        # 전역 변수 초기화
        global_queues.csv_file_handler = None
        global_queues.csv_writer = None
        global_queues.csv_file_path = None
