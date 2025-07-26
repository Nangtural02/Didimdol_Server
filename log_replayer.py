# log_replayer.py
import asyncio
import json
from pathlib import Path
from dataclasses import asdict

import global_queues
from data_models import SensorData


def select_log_file() -> Path | None:
    """log 디렉터리의 .json 파일 목록을 보여주고 사용자의 선택을 받습니다."""
    log_dir = Path("./history")
    if not log_dir.exists():
        print("[Log Replayer] 'history' 디렉터리가 없습니다.")
        return None

    json_files = sorted(list(log_dir.glob("*.json")))
    if not json_files:
        print(f"[Log Replayer] '{log_dir}' 디렉터리에 .json 파일이 없습니다.")
        return None

    print("[Log Replayer] 재생할 로그 파일을 선택하세요:")
    for i, file_path in enumerate(json_files):
        print(f"  {i}: {file_path.name}")

    while True:
        try:
            choice = int(input("파일 번호를 입력하세요: "))
            if 0 <= choice < len(json_files):
                return json_files[choice]
            else:
                print("잘못된 번호입니다. 다시 입력해주세요.")
        except ValueError:
            print("숫자를 입력해주세요.")


async def log_file_replayer():
    """
    [테스트용 워커] 선택된 로그 파일을 무한히 반복하여 재생할 수 있습니다.
    재생이 끝나면 다시 시작 신호를 기다립니다.
    """
    # 1. 서버 시작 시 파일을 딱 한 번만 선택하고 로드합니다.
    log_file = select_log_file()
    if not log_file:
        return

    print(f"[Log Replayer] '{log_file.name}' 파일을 로드합니다...")
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            all_data_points_dict = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[Log Replayer] 파일 읽기 오류: {e}")
        return

    if not all_data_points_dict:
        print("[Log Replayer] 로그 파일이 비어있습니다.")
        return

    all_data_points_obj = [SensorData(**data_dict) for data_dict in all_data_points_dict]
    print(f"[Log Replayer] 파일 로드 완료. 총 {len(all_data_points_obj)}개의 데이터 포인트.")

    # ✅ 2. 무한 루프를 통해 워커가 종료되지 않고 계속 대기/재생 상태를 반복하게 합니다.
    while True:
        print(f"\n[Log Replayer] 재생 준비 완료. 앱에서 운동 시작 버튼을 기다립니다...")

        # ✅ 3. 'WAITING' 상태: 앱의 시작 신호가 올 때까지 여기서 대기합니다.
        await global_queues.START_REPLAY_EVENT.wait()

        print(f"[Log Replayer] 시작 신호 수신! 재생을 시작합니다.")

        # ✅ 4. 'REPLAYING' 상태: 메모리에 있는 데이터를 사용하여 재생합니다.
        previous_timestamp = all_data_points_obj[0].Timestamp
        for data_point in all_data_points_obj:
            # 사용자가 중간에 'stop'을 누르면 즉시 재생을 멈춥니다.
            if not global_queues.is_processing_active:
                print("[Log Replayer] 'stop_processing' 신호 감지. 재생을 중단합니다.")

                break

            current_timestamp = data_point.Timestamp
            delay = current_timestamp - previous_timestamp
            if delay > 0:
                await asyncio.sleep(delay)
            previous_timestamp = current_timestamp

            await asyncio.gather(
                global_queues.RAW_DATA_QUEUE.put(data_point),
                global_queues.LOGGING_QUEUE.put(data_point)
            )
            await broadcast_to_debug(json.dumps(asdict(data_point)))

        print(f"[Log Replayer] 파일 재생 완료.")

        # ✅ 5. 다음 루프를 위해 이벤트를 '초기화(clear)'하여 다시 대기 상태로 만듭니다.
        #    만약 사용자가 'stop'을 눌러 중간에 멈췄다면, app_handler가 이미 clear했을 것입니다.
        if global_queues.START_REPLAY_EVENT.is_set():
            global_queues.START_REPLAY_EVENT.clear()


async def broadcast_to_debug(data: str):
    """디버깅 웹소켓에만 브로드캐스트하는 헬퍼 함수."""
    if global_queues.debug_websockets:
        tasks = [client.send(data) for client in global_queues.debug_websockets]
        await asyncio.gather(*tasks, return_exceptions=True)