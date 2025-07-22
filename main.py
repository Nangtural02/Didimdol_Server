import asyncio
import sys
from quart import Quart
from endpoints import bp as main_blueprint
from segmenter import repetition_segmenter
from inference import inference_worker
from result_to_app import result_emitter
from logger import file_logging_worker

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
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
    app.add_background_task(file_logging_worker)
    app.add_background_task(repetition_segmenter)
    app.add_background_task(inference_worker)
    app.add_background_task(result_emitter)
    print("모든 데이터 처리 파이프라인 워커가 시작되었습니다.")

# --- 서버 실행 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True)