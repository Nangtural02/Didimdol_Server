import asyncio
import sys
import argparse
import logging
from quart import Quart
from endpoints import bp as main_blueprint
from segmenter import repetition_segmenter
from inference import inference_worker
from result_to_app import result_emitter
from logger import file_logging_worker
from serial_importer import serial_data_importer

parser = argparse.ArgumentParser(description="UWB 스쿼트 트레이너 서버")
parser.add_argument(
    "--use-serial",
    action="store_true", # 이 플래그가 있으면 True가 됨
    help="WebSocket 대신 시리얼 포트에서 데이터를 입력받습니다."
)
args = parser.parse_args()
# --- Quart 앱 생성 및 설정 ---
app = Quart(__name__, template_folder='templates')
app.register_blueprint(main_blueprint)

# --- 서버 시작/종료 이벤트 핸들러 ---
@app.before_serving
async def startup():
    """
    서버 시작 시 모든 백그라운드 파이프라인 워커들을 실행합니다.
    """
    print("서버 시작... 백그라운드 워커를 실행합니다.")

    # ✅ --use-serial 플래그가 있을 때만 시리얼 임포터 실행
    if args.use_serial:
        print(">> 시리얼 포트 입력 모드로 실행합니다.")
        app.add_background_task(serial_data_importer)
    else:
        print(">> WebSocket 입력 모드로 실행합니다. (기본값)")

    app.add_background_task(file_logging_worker)
    app.add_background_task(repetition_segmenter)
    app.add_background_task(inference_worker)
    app.add_background_task(result_emitter)
    print("모든 데이터 처리 파이프라인 워커가 시작되었습니다.")

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