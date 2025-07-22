import asyncio
import json
import time
import datetime as dt
from pathlib import Path

# ✅ websocket 객체를 직접 임포트합니다.
from quart import Quart, websocket, render_template

# --- Quart 애플리케이션 초기화 ---
app = Quart(__name__, template_folder='templates')

# --- 서버 상태 및 연결 관리를 위한 전역 변수 ---
sensor_websockets = set()
app_websockets = set()
debug_websockets = set()

# 비동기 데이터 처리를 위한 큐
DATA_QUEUE = asyncio.Queue()

# 모바일 앱에서 제어할 녹화 상태 플래그
is_recording = False
log_file_handler = None
log_file_path = None
# JSON 배열에서 첫 항목인지 확인하는 플래그
is_first_entry_in_file = True


def parse_sensor_data(s: str) -> dict:
    """UWB 앵커에서 받은 문자열 메시지를 파싱하여 딕셔너리로 변환합니다."""
    # 원본 메시지가 비어있는 경우 조기 반환
    if not s or not s.strip():
        return None

    original_message = s  # 로그 출력을 위해 원본 메시지 저장

    if s.startswith('{') and s.endswith('}'):
        s = s[1:-1]

    p = [x.strip() for x in s.split(",")]

    # ✅ 수정된 부분: 패킷 길이가 맞지 않을 때 원본 내용 출력
    if len(p) != 12:
        print(f"[Parser] Invalid packet length: {len(p)}. Content: '{original_message}'")
        return None

    try:
        tag, seq, dist = int(p[0]), int(p[1]), float(p[2])
        ax, ay, az = map(int, p[3:6])
        gx, gy, gz = map(int, p[6:9])
        mx, my, mz = map(int, p[9:12])
    # ✅ 수정된 부분: 파싱 중 값 변환 오류 시 원본 내용 출력
    except (ValueError, IndexError) as e:
        print(f"[Parser] Error parsing packet: {e}. Content: '{original_message}'")
        return None

    return {
        "Timestamp": time.time(),
        "TagAddr": tag, "Seq": seq, "Distance": dist,
        "ax": ax, "ay": ay, "az": az,
        "gx": gx, "gy": gy, "gz": gz,
        "mx": mx, "my": my, "mz": mz,
    }


# --- 여러 클라이언트에게 메시지를 보내는 브로드캐스트 함수 ---
async def broadcast(data: str, clients: set):
    """지정된 클라이언트 그룹에 메시지를 비동기적으로 전송합니다."""
    tasks = [client.send(data) for client in clients]
    await asyncio.gather(*tasks, return_exceptions=True)


# --- WebSocket 엔드포인트 정의 ---

@app.websocket('/sensor')
async def sensor_handler():
    """UWB 앵커(헬스케어 장비)가 연결되는 엔드포인트."""
    client = websocket._get_current_object()  # 현재 연결된 클라이언트 객체 가져오기
    sensor_websockets.add(client)
    print(f"[Sensor WS] New connection from {client.remote_addr}. Total: {len(sensor_websockets)}")
    try:
        # ✅ 수정된 부분: async for -> while True + websocket.receive()
        while True:
            message = await client.receive()
            parsed_data = parse_sensor_data(message)

            if parsed_data:
                await DATA_QUEUE.put(parsed_data)
                await broadcast(json.dumps(parsed_data), debug_websockets)

    except asyncio.CancelledError:
        print(f"[Sensor WS] Client {client.remote_addr} disconnected.")
    finally:
        sensor_websockets.remove(client)
        print(f"[Sensor WS] Connection closed. Total: {len(sensor_websockets)}")


@app.websocket('/app')
async def app_handler():
    """모바일 애플리케이션이 연결되는 엔드포인트."""
    global is_recording, log_file_handler, log_file_path, is_first_entry_in_file

    client = websocket._get_current_object()
    app_websockets.add(client)
    print(f"[App WS] New connection from {client.remote_addr}. Total: {len(app_websockets)}")
    try:
        # ✅ 수정된 부분: async for -> while True + websocket.receive()
        while True:
            message = await client.receive()
            try:
                command = json.loads(message)
                print(f"[App WS] Received command: {command}")

                if command.get("command") == "start_recording":
                    is_recording = True
                    is_first_entry_in_file = True  # 새 파일 시작 플래그
                    log_dir = Path("../log")
                    log_dir.mkdir(exist_ok=True)
                    now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{command.get('user', 'unknown')}_{command.get('session', '1')}_{now_str}.json"
                    log_file_path = log_dir / filename
                    # 파일을 열고 JSON 배열 시작
                    log_file_handler = log_file_path.open("w", encoding="utf-8")
                    log_file_handler.write("[\n")
                    print(f"Recording started. File: {log_file_path}")
                    await client.send(json.dumps({"status": "recording_started", "file": str(log_file_path)}))

                elif command.get("command") == "stop_recording":
                    if is_recording and log_file_handler:
                        # 파일에 내용이 있을 경우에만 마지막 쉼표를 제거하고 닫기
                        if not is_first_entry_in_file:
                            # 마지막 ',\n' 을 지우기 위해 파일 포인터 이동
                            log_file_handler.seek(log_file_handler.tell() - 2)

                        log_file_handler.write("\n]\n")
                        log_file_handler.close()
                        print(f"Recording stopped. File saved: {log_file_path}")
                        await client.send(json.dumps({"status": "recording_stopped", "file": str(log_file_path)}))

                    is_recording = False
                    log_file_handler = None

            except json.JSONDecodeError:
                print(f"[App WS] Invalid JSON from app: {message}")
            except Exception as e:
                print(f"[App WS] Error processing command: {e}")

    except asyncio.CancelledError:
        print(f"[App WS] Client {client.remote_addr} disconnected.")
    finally:
        app_websockets.remove(client)
        if is_recording and log_file_handler:
            is_recording = False
            log_file_handler.close()
            print("[App WS] App disconnected, recording stopped and file closed.")
        print(f"[App WS] Connection closed. Total: {len(app_websockets)}")


@app.websocket('/ws_debug')
async def debug_handler():
    """디버깅용 웹페이지가 연결되는 엔드포인트."""
    try:
        # ✅ 1. 연결을 명시적으로 수락합니다.
        # 이 호출이 성공해야 클라이언트의 onopen 이벤트가 발생합니다.
        await websocket.accept()

        # 2. 클라이언트 객체를 연결된 소켓 목록에 추가합니다.
        # accept() 이후에 추가해야 성공적으로 연결된 클라이언트만 관리할 수 있습니다.
        client = websocket._get_current_object()
        debug_websockets.add(client)
        print(f"[Debug WS] New connection from {client.remote_addr}. Total: {len(debug_websockets)}")

        # 3. 연결이 끊길 때까지 대기합니다.
        await asyncio.Future()

    except asyncio.CancelledError:
        print(f"[Debug WS] Client {websocket.remote_addr} disconnected.")
    finally:
        # 연결이 끊기면 목록에서 제거
        if 'client' in locals() and client in debug_websockets:
            debug_websockets.remove(client)
        print(f"[Debug WS] Connection closed. Total: {len(debug_websockets)}")

@app.route('/')
async def index():
    """디버깅용 웹페이지를 렌더링합니다."""
    return await render_template("index.html")


async def data_processor():
    """
    DATA_QUEUE에 쌓인 데이터를 처리하는 백그라운드 작업.
    """
    global is_first_entry_in_file
    while True:
        try:
            data = await DATA_QUEUE.get()

            if is_recording and log_file_handler:
                if not is_first_entry_in_file:
                    log_file_handler.write(",\n")

                # indent=None으로 변경하여 한 줄로 저장 (파일 크기 감소)
                log_file_handler.write(json.dumps(data, ensure_ascii=False))
                is_first_entry_in_file = False

            # (딥러닝 모델 추론 로직 추가 위치)

            DATA_QUEUE.task_done()

        except Exception as e:
            print(f"[Processor] Error in data processor: {e}")


@app.before_serving
async def startup():
    app.add_background_task(data_processor)
    print("Background data processor started.")


@app.after_serving
async def shutdown():
    if log_file_handler:
        log_file_handler.close()
    print("Server shutting down. Log file closed if open.")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True)