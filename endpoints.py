import asyncio
import json
import datetime as dt
from pathlib import Path
from dataclasses import asdict
from quart import Blueprint, websocket, render_template
from utils import parse_sensor_data
import global_queues
from global_queues import LOGGING_QUEUE, RAW_DATA_QUEUE, sensor_websockets, app_websockets, debug_websockets
from data_models import SensorData

bp = Blueprint('main_endpoints', __name__)

async def broadcast(data: str, clients: set):
    tasks = [client.send(data) for client in clients]
    await asyncio.gather(*tasks, return_exceptions=True)

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
                    global_queues.is_processing_active = True
                    if global_queues.server_operating_mode == "normal":
                        global_queues.is_first_entry_in_file = True
                        # 파일 열기 로직은 동일
                        log_dir = Path("./log")
                        log_dir.mkdir(exist_ok=True)
                        now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"{command.get('user', 'unknown')}_{command.get('session', '1')}_{now_str}.json"
                        log_file_path = log_dir / filename
                        log_file_handler = log_file_path.open("w", encoding="utf-8")
                        log_file_handler.write("[\n")

                        print(f"Processing started. Logging to: {log_file_path}")
                        await client.send(json.dumps({"status": "processing_started"}))
                    elif global_queues.server_operating_mode == "replay":
                        if not global_queues.START_REPLAY_EVENT.is_set():
                            global_queues.START_REPLAY_EVENT.set()
                        print(f"Replaying started.")
                        await client.send(json.dumps({"status": "replay_started"}))

                # ✅ "stop_processing" 명령 처리
                elif command.get("command") == "stop_processing":
                    if not global_queues.is_processing_active:
                        await client.send(json.dumps({"status": "error", "message": "Not processing."}))
                        continue
                    global_queues.is_processing_active = False
                    if global_queues.server_operating_mode == "normal":

                        # 파일 닫기 로직은 동일
                        if global_queues.log_file_handler:
                            if not global_queues.is_first_entry_in_file:
                                global_queues.log_file_handler.seek(global_queues.log_file_handler.tell() - 2)
                            global_queues.log_file_handler.write("\n]\n")
                            global_queues.log_file_handler.close()

                            print(f"Processing stopped. Log file saved: {global_queues.log_file_path}")
                            await client.send(json.dumps({"status": "processing_stopped", "file": str(log_file_path)}))
                        log_file_handler = None
                        log_file_path = None
                    elif global_queues.server_operating_mode == "replay":
                        if global_queues.START_REPLAY_EVENT.is_set():
                            global_queues.START_REPLAY_EVENT.clear()


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