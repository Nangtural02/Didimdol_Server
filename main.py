import asyncio
import sys
import argparse
import logging
from quart import Quart

import global_queues
from endpoints import bp as main_blueprint
from segmenter import repetition_segmenter
from inference import inference_worker
from result_to_app import result_emitter
from logger import file_logging_worker
from serial_importer import serial_data_importer
from log_replayer import log_file_replayer
from manual_segmenter import manual_repetition_collector
from csv_logger import csv_result_logger

parser = argparse.ArgumentParser(description="UWB 스쿼트 트레이너 서버")
input_mode = parser.add_mutually_exclusive_group() # ✅ 여러 모드 중 하나만 선택 가능
input_mode.add_argument(
    "--use-serial",
    action="store_true",
    help="WebSocket 대신 시리얼 포트에서 데이터를 입력받습니다."
)
input_mode.add_argument(
    "--replay-log",
    action="store_true",
    help="저장된 로그 파일을 재생하여 데이터를 입력받습니다."
)
input_mode.add_argument(
    "--model-test",
    action="store_true",
    help="수동 모델 평가 모드를 실행합니다 (시리얼 입력 필요)."
)
args = parser.parse_args()
# --- Quart 앱 생성 및 설정 ---
app = Quart(__name__, template_folder='templates')
app.register_blueprint(main_blueprint)


@app.before_serving
async def startup():
    """서버 시작 시 모드에 맞는 백그라운드 워커들을 실행합니다."""
    print("서버 시작... 백그라운드 워커를 실행합니다.")

    # --- 입력 소스 워커 설정 ---
    if args.use_serial:
        global_queues.server_operating_mode = "serial"
        print(">> 입력 모드: 시리얼 포트")
        app.add_background_task(serial_data_importer)
    elif args.replay_log:
        global_queues.server_operating_mode = "replay"
        print(">> 입력 모드: 로그 파일 재생")
        # log_replayer.py의 log_file_replayer를 임포트해서 사용해야 함
        from log_replayer import log_file_replayer
        app.add_background_task(log_file_replayer)
    elif args.model_test:
        global_queues.server_operating_mode = "model-test"
        print(">> 입력 모드: 모델 테스트 (시리얼 입력)")
        # 모델 테스트는 시리얼 입력을 전제로 함
        app.add_background_task(serial_data_importer)
    else:
        # 아무 인자가 없으면 WebSocket 모드가 기본값
        global_queues.server_operating_mode = "Normal"
        print(">> 입력 모드: WebSocket (Normal)")

    # --- 데이터 처리 및 출력 워커 설정 ---
    if args.model_test:
        # 모델 테스트 모드에서는 수동 세그멘터와 CSV 로거만 실행
        app.add_background_task(manual_repetition_collector)
        app.add_background_task(csv_result_logger)
    else:
        # 일반 모드에서는 자동 세그멘터, JSON 로거, 앱 결과 전송기 실행
        app.add_background_task(file_logging_worker)
        app.add_background_task(repetition_segmenter)
        app.add_background_task(result_emitter)

    # AI 추론 워커는 모든 모드에서 공통으로 필요함
    app.add_background_task(inference_worker)

    print("모든 데이터 처리 워커가 시작되었습니다.")

# --- 서버 실행 ---
if __name__ == '__main__':
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    access_logger = logging.getLogger("hypercorn.access")
    access_logger.setLevel(logging.DEBUG)

    error_logger = logging.getLogger("hypercorn.error")
    error_logger.setLevel(logging.DEBUG)

    # 서버 실행
    app.run(host='0.0.0.0', port=5050, debug=True)