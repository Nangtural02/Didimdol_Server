import asyncio
import json
import csv
import datetime as dt
from pathlib import Path
from dataclasses import asdict
from quart import Blueprint, websocket, render_template
from utils import parse_sensor_data, clear_all_queues
import global_queues
from global_queues import LOGGING_QUEUE, RAW_DATA_QUEUE, sensor_websockets, app_websockets, debug_websockets
from data_models import SensorData, SquatSegment, InferenceResult

bp = Blueprint('main_endpoints', __name__)

async def broadcast(data: str, clients: set):
    tasks = [client.send(data) for client in clients]
    await asyncio.gather(*tasks, return_exceptions=True)


# ✅ [새 기능] 모델 테스트 UI를 제공하는 HTTP 엔드포인트
@bp.route('/model-test')
async def model_test_page():
    return await render_template("model_test.html")


@bp.websocket('/model-test-ws')
async def model_test_ws_handler():
    """웹 UI로부터 모델 테스트 제어 명령을 수신합니다."""
    client = websocket._get_current_object()
    print("[Model Test WS] 제어용 웹 UI 연결됨.")
    try:
        while True:
            message = await client.receive()
            command = json.loads(message)
            print(f"[Model Test WS] Received command: {command}")

            cmd = command.get("command")

            # --- 전체 측정 제어 ---
            if cmd == "start_overall_test":
                if global_queues.is_processing_active:
                    await client.send(json.dumps({"status": "Error", "message": "Test is already running."}))
                    continue

                await clear_all_queues(
                    global_queues.RAW_DATA_QUEUE,
                    global_queues.SEGMENT_QUEUE,
                    global_queues.RESULT_QUEUE
                )
                global_queues.repetition_count = 0
                global_queues.is_processing_active = True

                # ✅ [온전한 코드] CSV 파일 열기 및 DictWriter 설정
                log_dir = Path("./log");
                log_dir.mkdir(exist_ok=True)
                now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                # CSV 파일명 생성 (사용자 정보는 웹 UI에서 받지 않으므로 'model_test'로 고정)
                base_filename = f"model_test_session_{now_str}"
                global_queues.csv_file_path = log_dir / f"{base_filename}_results.csv"

                # 파일을 열고 핸들러를 전역 변수에 저장
                global_queues.csv_file_handler = global_queues.csv_file_path.open("w", encoding="utf-8", newline="")

                # CSV 헤더(필드명) 정의. InferenceResult의 필드와 일치해야 함.
                fieldnames = [field.name for field in InferenceResult.__dataclass_fields__.values()]

                # DictWriter 객체를 생성하고 헤더를 씀
                global_queues.csv_writer = csv.DictWriter(global_queues.csv_file_handler, fieldnames=fieldnames)
                global_queues.csv_writer.writeheader()

                print(f"Model test started. Saving results to: {global_queues.csv_file_path}")
                await client.send(json.dumps({"status": "Overall test started"}))

            elif cmd == "stop_overall_test":
                if not global_queues.is_processing_active:
                    continue

                global_queues.is_processing_active = False

                # ✅ [온전한 코드] CSV 파일 핸들러 닫기
                if global_queues.csv_file_handler:
                    global_queues.csv_file_handler.close()
                    print(f"CSV log saved to: {global_queues.csv_file_path}")

                # 전역 변수 초기화
                global_queues.csv_file_handler = None
                global_queues.csv_writer = None
                global_queues.csv_file_path = None

                await client.send(json.dumps({"status": "Overall test stopped"}))

            # --- 1회 스쿼트 측정 제어 ---
            elif cmd == "start_rep":
                if not global_queues.is_processing_active:
                    await client.send(json.dumps({"status": "Error: Overall test not started"}))
                    continue
                global_queues.is_rep_recording_active = True
                global_queues.rep_data_buffer.clear()
                await client.send(json.dumps({"status": "Repetition recording started"}))

            elif cmd == "stop_rep":
                if not global_queues.is_rep_recording_active:
                    continue
                global_queues.is_rep_recording_active = False

                if global_queues.rep_data_buffer:
                    global_queues.repetition_count += 1
                    squat_event = SquatSegment(
                        repetition_count=global_queues.repetition_count,
                        start_timestamp=global_queues.rep_data_buffer[0].Timestamp,
                        data=list(global_queues.rep_data_buffer)
                    )
                    await global_queues.SEGMENT_QUEUE.put(squat_event)
                    print(f"[Model Test] {global_queues.repetition_count}번째 수동 세그먼트를 큐에 추가함.")
                    await client.send(json.dumps({
                        "status": "Repetition stopped",
                        "rep_count": global_queues.repetition_count
                    }))
                global_queues.rep_data_buffer.clear()

    except asyncio.CancelledError:
        print("[Model Test WS] 제어용 웹 UI 연결 끊김.")
    finally:
        # 비정상 종료 시 테스트 강제 종료 및 파일 닫기
        if global_queues.is_processing_active:
            global_queues.is_processing_active = False
            if global_queues.csv_file_handler:
                global_queues.csv_file_handler.close()
            print("[Model Test WS] Connection closed, forcefully stopped test.")
@bp.websocket('/sensor')
async def sensor_handler():
    """수신된 데이터를 is_processing_active 상태에 따라 처리하거나 버립니다."""
    if global_queues.server_operating_mode != "Normal":
        print("Serial Mode. Web Connection is not available now.")
        return
    client = websocket._get_current_object()
    sensor_websockets.add(client)
    try:
        while True:
            message = await client.receive()
            parsed_data = parse_sensor_data(message)

            if parsed_data:
                # ✅ [핵심] 처리 활성화 상태일 때만 데이터를 큐에 넣습니다.
                if global_queues.is_processing_active:
                    await asyncio.gather(
                        RAW_DATA_QUEUE.put(parsed_data),
                        LOGGING_QUEUE.put(parsed_data)
                    )
                # else:  # 처리 비활성화 상태이면 데이터를 버립니다. (아무것도 안 함)

                # 디버깅 UI는 항상 데이터를 볼 수 있도록 그대로 둡니다.
                await broadcast(json.dumps(asdict(parsed_data)), debug_websockets)
    finally:
        sensor_websockets.remove(client)


@bp.websocket('/app')
async def app_handler():
    """앱 클라이언트의 처리 시작/중단 제어 명령을 수신합니다."""
    client = websocket._get_current_object()
    app_websockets.add(client)
    try:
        while True:
            message = await client.receive()
            try:
                command = json.loads(message)
                print(f"[App WS] Received command: {command}")

                # ✅ "start_processing" 명령 처리
                if command.get("command") == "start_processing":
                    if global_queues.is_processing_active:
                        await client.send(json.dumps({"status": "error", "message": "Already processing."}))
                        continue
                    await clear_all_queues(
                        global_queues.RAW_DATA_QUEUE,
                        global_queues.LOGGING_QUEUE,
                        global_queues.SEGMENT_QUEUE,
                        global_queues.RESULT_QUEUE
                    )
                    global_queues.repetition_count = 0
                    global_queues.is_processing_active = True

                    log_dir = Path("./log");
                    log_dir.mkdir(exist_ok=True)
                    now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    base_filename = f"{command.get('user', 'unknown')}_{command.get('session', '1')}_{now_str}"

                    # 결과 로그 저장은 모든 모드에서 공통으로 수행
                    global_queues.is_first_entry_in_results_file = True
                    global_queues.result_log_file_path = log_dir / f"{base_filename}_results.json"
                    global_queues.result_log_file_handler = global_queues.result_log_file_path.open("w",
                                                                                                    encoding="utf-8")
                    global_queues.result_log_file_handler.write("[\n")
                    print(f"Result logging started to: {global_queues.result_log_file_path}")

                    # ✅ "Normal"과 "serial" 모드에서만 원본 데이터 로깅
                    if global_queues.server_operating_mode in ["Normal", "serial"]:
                        global_queues.is_first_entry_in_file = True
                        global_queues.log_file_path = log_dir / f"{base_filename}_raw.json"
                        global_queues.log_file_handler = global_queues.log_file_path.open("w", encoding="utf-8")
                        global_queues.log_file_handler.write("[\n")
                        print(f"Raw data logging started to: {global_queues.log_file_path}")
                        await client.send(json.dumps({"status": "processing_started"}))

                    elif global_queues.server_operating_mode == "replay":
                        if not global_queues.START_REPLAY_EVENT.is_set():
                            global_queues.START_REPLAY_EVENT.set()
                        print(f"Replaying started.")
                        await client.send(json.dumps({"status": "replay_started"}))

                elif command.get("command") == "stop_processing":
                    if not global_queues.is_processing_active:
                        await client.send(json.dumps({"status": "error", "message": "Not processing."}))
                        continue

                    global_queues.is_processing_active = False

                    # 결과 로그 파일 닫기는 모든 모드에서 공통
                    if global_queues.result_log_file_handler:
                        if not global_queues.is_first_entry_in_results_file:
                            global_queues.result_log_file_handler.seek(global_queues.result_log_file_handler.tell() - 2)
                        global_queues.result_log_file_handler.write("\n]\n")
                        global_queues.result_log_file_handler.close()
                        print(f"Result log file saved: {global_queues.result_log_file_path}")
                        global_queues.result_log_file_handler = None
                        global_queues.result_log_file_path = None

                    # ✅ "Normal"과 "serial" 모드에서만 원본 데이터 로그 파일 닫기
                    if global_queues.server_operating_mode in ["Normal", "serial"]:
                        if global_queues.log_file_handler:
                            if not global_queues.is_first_entry_in_file:
                                global_queues.log_file_handler.seek(global_queues.log_file_handler.tell() - 2)
                            global_queues.log_file_handler.write("\n]\n")
                            global_queues.log_file_handler.close()
                            print(f"Raw data log file saved: {global_queues.log_file_path}")
                            await client.send(
                                json.dumps({"status": "processing_stopped", "file": str(global_queues.log_file_path)}))
                        global_queues.log_file_handler = None
                        global_queues.log_file_path = None

                    elif global_queues.server_operating_mode == "replay":
                        if global_queues.START_REPLAY_EVENT.is_set():
                            global_queues.START_REPLAY_EVENT.clear()
                        await client.send(json.dumps({"status": "replay_stopped"}))

            except Exception as e:
                print(f"[App WS] Error processing command: {e}")

    finally:
        # 앱 연결이 끊기면 안전하게 처리 중단
        if global_queues.is_processing_active:
            is_processing_active = False
            if global_queues.log_file_handler:
                global_queues.log_file_handler.close()
            print(f"[App WS] Client disconnected, forcefully stopped processing.")
        app_websockets.remove(client)
@bp.websocket('/ws_debug')
async def debug_handler():
    try:
        await websocket.accept()
        client = websocket._get_current_object()
        debug_websockets.add(client)
        await asyncio.Future()
    finally:
        if 'client' in locals() and client in debug_websockets:
            debug_websockets.remove(client)

@bp.route('/')
async def index():
    return await render_template("index.html")