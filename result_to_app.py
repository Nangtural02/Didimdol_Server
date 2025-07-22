import asyncio
import json
from dataclasses import asdict

from global_queues import RESULT_QUEUE, app_websockets
from data_models import InferenceResult


async def result_emitter():
    """[파이프라인 4단계] RESULT_QUEUE에서 결과를 꺼내 앱으로 전송합니다."""
    print("[Emitter] 결과 전송기 시작됨.")
    while True:
        result: InferenceResult = await RESULT_QUEUE.get()
        json_result = json.dumps(asdict(result), ensure_ascii=False)

        # ✅ 등록된 모든 앱 클라이언트에게 결과 브로드캐스트
        if app_websockets:
            tasks = [client.send(json_result) for client in app_websockets]
            await asyncio.gather(*tasks, return_exceptions=True)
            print(f"[Emitter] {len(app_websockets)}개의 앱으로 결과 전송: {json_result}")
        else:
            print(f"[Emitter] 전송할 앱 클라이언트가 없음. 결과 건너뜀: {json_result}")
        RESULT_QUEUE.task_done()