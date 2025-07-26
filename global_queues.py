import asyncio
import csv
from typing import Optional
from data_models import InferenceResult

RAW_DATA_QUEUE = asyncio.Queue()
LOGGING_QUEUE = asyncio.Queue()
SEGMENT_QUEUE = asyncio.Queue(maxsize=10)
RESULT_QUEUE = asyncio.Queue(maxsize=10)

sensor_websockets = set()
app_websockets = set()
debug_websockets = set()

repetition_count = 0
is_processing_active = False

START_REPLAY_EVENT = asyncio.Event()

log_file_handler = None
log_file_path = None
is_first_entry_in_file = True

result_log_file_handler = None
result_log_file_path = None
is_first_entry_in_results_file = True

# ✅ 모델 테스트 모드 전용 상태 변수
is_rep_recording_active = False  # 1회 스쿼트 동작 녹화 여부
rep_data_buffer = []             # 1회 스쿼트 데이터 임시 저장 버퍼

# ✅ CSV 로깅을 위한 핸들러
csv_file_handler = None
csv_writer: csv.DictWriter | None = None
csv_file_path = None
last_inference_result: Optional[InferenceResult] = None
NEW_RESULT_EVENT = asyncio.Event()

server_operating_mode = "Normal"