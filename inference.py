import asyncio
import random

from global_queues import SEGMENT_QUEUE, RESULT_QUEUE
from data_models import SquatSegment, InferenceResult, SensorData


async def run_ai_inference_placeholder(data_segment: list[SensorData]) -> InferenceResult:
    """
    todo: AI 모델 추론 함수
    AI 모델 추론 함수 스켈레톤. 지금은 랜덤한 InferenceResult 객체를 생성합니다.
    """
    head_status = random.choice([0, 1, 2])
    spine_status = random.choice([0, 1, 2])
    knee_status = random.choice([0, 1, 2])
    feet_status = random.choice([0, 1, 2])

    total_status_sum = head_status + spine_status + knee_status + feet_status
    score = max(0, 100 - int(total_status_sum * (100 / 8)))

    return InferenceResult(
        count=0,
        head=head_status, spine=spine_status, knees=knee_status, feet=feet_status,
        totalScore=score
    )


async def inference_worker():
    """[파이프라인 3단계] SEGMENT_QUEUE에서 데이터를 꺼내 AI 추론하는 함수 호출 후,
     결과를 RESULT_QUEUE에 넣습니다."""
    print("[Inference] 추론 워커 시작됨.")
    while True:
        squat_event: SquatSegment = await SEGMENT_QUEUE.get()
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 시작...")

        result = await run_ai_inference_placeholder(squat_event.data)
        result.count = squat_event.repetition_count

        await RESULT_QUEUE.put(result)
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 완료. 결과를 RESULT_QUEUE에 추가함.")
        SEGMENT_QUEUE.task_done()