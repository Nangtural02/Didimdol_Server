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
    [테스트용 워커] 선택된 JSON 로그 파일을 읽어,
    실제 시간 간격에 맞춰 파이프라인에 데이터를 주입합니다.
    """
    log_file = select_log_file()
    if not log_file:
        return

    print(f"[Log Replayer] '{log_file.name}' 파일 재생을 시작합니다...")
    print("앱에서 'start_processing' 명령을 보내면 데이터 주입이 시작됩니다.")

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            all_data_points = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[Log Replayer] 파일 읽기 오류: {e}")
        return

    if not all_data_points:
        print("[Log Replayer] 로그 파일이 비어있습니다.")
        return

    # 첫 번째 데이터 포인트의 타임스탬프를 기준으로 시간 간격 계산
    previous_timestamp = all_data_points[0].get("Timestamp")

    for data_dict in all_data_points:
        # data_dict를 SensorData 객체로 변환 (타입 검증)
        try:
            # ✅ data_models에 정의된 타입과 일치하도록 값을 변환
            data_point = SensorData(
                Timestamp=float(data_dict['Timestamp']),
                TagAddr=int(data_dict['TagAddr']),
                Seq=int(data_dict['Seq']),
                Distance=float(data_dict['Distance']),
                ax=float(data_dict['ax']), ay=float(data_dict['ay']), az=float(data_dict['az']),
                gx=float(data_dict['gx']), gy=float(data_dict['gy']), gz=float(data_dict['gz']),
                mx=float(data_dict['mx']), my=float(data_dict['my']), mz=float(data_dict['mz'])
            )
        except (KeyError, ValueError) as e:
            print(f"[Log Replayer] 데이터 파싱 오류, 건너뜀: {e} - {data_dict}")
            continue

        # 처리 활성화 상태가 될 때까지 기다림
        while not global_queues.is_processing_active:
            await asyncio.sleep(0.5)

        # ✅ 실제 시간 간격만큼 대기
        current_timestamp = data_point.Timestamp
        if previous_timestamp is not None:
            delay = current_timestamp - previous_timestamp
            if delay > 0:
                await asyncio.sleep(delay)
        previous_timestamp = current_timestamp

        # 데이터를 큐에 주입
        await asyncio.gather(
            global_queues.RAW_DATA_QUEUE.put(data_point),
            global_queues.LOGGING_QUEUE.put(data_point)
        )

        # 디버깅 UI에도 전송
        await broadcast_to_debug(json.dumps(asdict(data_point)))

    print(f"[Log Replayer] '{log_file.name}' 파일 재생 완료.")


async def broadcast_to_debug(data: str):
    """디버깅 웹소켓에만 브로드캐스트하는 헬퍼 함수."""
    if global_queues.debug_websockets:
        tasks = [client.send(data) for client in global_queues.debug_websockets]
        await asyncio.gather(*tasks, return_exceptions=True)