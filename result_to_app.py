import asyncio
import json
from dataclasses import asdict

import global_queues
from data_models import InferenceResult


async def result_emitter():
    """
    [파이프라인 4단계] RESULT_QUEUE에서 결과를 꺼내
    1. 앱으로 전송하고, 2. 파일에 저장합니다.
    """
    print("[Emitter] 결과 전송/저장기 시작됨.")
    while True:
        result: InferenceResult = await global_queues.RESULT_QUEUE.get()

        try:
            json_result = json.dumps(asdict(result), ensure_ascii=False)

            # 1. 앱 클라이언트로 결과 브로드캐스트
            if global_queues.app_websockets:
                tasks = [client.send(json_result) for client in global_queues.app_websockets]
                await asyncio.gather(*tasks, return_exceptions=True)
                print(f"[Emitter] {len(global_queues.app_websockets)}개의 앱으로 결과 전송: {json_result}")
            else:
                print(f"[Emitter] 전송할 앱 클라이언트가 없음. 결과 건너뜀.")

            # ✅ 2. 결과 파일에 로그 기록
            if global_queues.is_processing_active and global_queues.result_log_file_handler:
                if not global_queues.is_first_entry_in_results_file:
                    global_queues.result_log_file_handler.write(",\n")

                global_queues.result_log_file_handler.write(json_result)
                global_queues.is_first_entry_in_results_file = False

        finally:
            global_queues.RESULT_QUEUE.task_done()