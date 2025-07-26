# manual_segmenter.py
import asyncio
import global_queues
from data_models import SensorData

async def manual_repetition_collector():
    """
    [모델 테스트 모드용] is_rep_recording_active가 True일 때만
    RAW_DATA_QUEUE의 데이터를 임시 버퍼에 저장합니다.
    """
    print("[Manual Segmenter] 수동 동작 수집기 시작됨.")
    while True:
        # RAW_DATA_QUEUE에 데이터가 들어오면 항상 가져옴
        data_point: SensorData = await global_queues.RAW_DATA_QUEUE.get()
        try:
            # 1회 스쿼트 녹화가 활성화된 상태일 때만 버퍼에 추가
            if global_queues.is_rep_recording_active:
                global_queues.rep_data_buffer.append(data_point)
        finally:
            global_queues.RAW_DATA_QUEUE.task_done()