# segmenter.py
import asyncio
import time

from global_queues import RAW_DATA_QUEUE, SEGMENT_QUEUE
from data_models import SensorData, SquatSegment


async def repetition_segmenter():
    """[파이프라인 2단계] 100개 데이터를 묶어 SquatSegment 객체로 만들어 SEGMENT_QUEUE에 넣습니다."""
    print("[Segmenter] 동작 분할기 시작됨.")
    rep_count = 0
    while True:
        segment_data: list[SensorData] = []

        first_data_point = await RAW_DATA_QUEUE.get()
        segment_data.append(first_data_point)
        start_time = first_data_point.Timestamp
        RAW_DATA_QUEUE.task_done()

        for _ in range(99):
            data_point = await RAW_DATA_QUEUE.get()
            segment_data.append(data_point)
            RAW_DATA_QUEUE.task_done()

        rep_count += 1
        squat_event = SquatSegment(
            repetition_count=rep_count,
            start_timestamp=start_time,
            data=segment_data
        )
        await SEGMENT_QUEUE.put(squat_event)
        print(f"[Segmenter] {rep_count}번째 동작(SquatSegment)을 SEGMENT_QUEUE에 추가함.")