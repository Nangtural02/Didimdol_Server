import asyncio
import serial
import serial.tools.list_ports
from dataclasses import asdict
import json

import global_queues
from global_queues import RAW_DATA_QUEUE, LOGGING_QUEUE, debug_websockets
from utils import parse_sensor_data
from endpoints import broadcast  # endpoints에서 broadcast 함수를 가져옴


def select_serial_port() -> str | None:
    """사용 가능한 시리얼 포트를 나열하고 사용자에게 선택하도록 요청합니다."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("[Serial Importer] 사용 가능한 시리얼 포트가 없습니다.")
        return None

    print("[Serial Importer] 사용 가능한 시리얼 포트:")
    for i, port in enumerate(ports):
        print(f"  {i}: {port.device} - {port.description}")

    while True:
        try:
            choice = int(input("사용할 포트 번호를 입력하세요: "))
            if 0 <= choice < len(ports):
                return ports[choice].device
            else:
                print("잘못된 번호입니다. 다시 입력해주세요.")
        except ValueError:
            print("숫자를 입력해주세요.")


def blocking_serial_reader(port: str, loop: asyncio.AbstractEventLoop):
    """
    별도 스레드에서 실행될 블로킹 시리얼 리더 함수.
    읽은 데이터를 메인 이벤트 루프로 안전하게 전달합니다.
    """
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        print(f"[Serial Importer] {port} 포트에서 데이터 수신 대기 중...")
    except serial.SerialException as e:
        print(f"[Serial Importer] 시리얼 포트를 여는 데 실패했습니다: {e}")
        return

    while True:
        try:
            # ✅ 수정: readline() 대신 read_until() 사용
            # '}' 문자를 만날 때까지 데이터를 읽어들입니다.
            line = ser.read_until(b'}')

            # # ✅ [디버깅] 실제로 읽은 바이트를 그대로 출력해봅니다.
            # if line:
            #     print(f"[Serial Importer-DEBUG] Raw bytes received: {line}")

            # 읽은 데이터가 있을 경우에만 처리
            if not line:
                continue

            # 메인 이벤트 루프에서 실행될 코루틴을 스케줄링
            loop.call_soon_threadsafe(
                asyncio.create_task,
                process_serial_line(line.decode('utf-8', errors='ignore'))
            )
        except serial.SerialException:
            print("[Serial Importer] 시리얼 연결이 끊겼습니다.")
            break
        except Exception as e:
            print(f"[Serial Importer] 읽기 중 오류 발생: {e}")
            break
    ser.close()


async def process_serial_line(line: str):
    """
    메인 이벤트 루프에서 실행되는 함수. 파싱 및 큐에 데이터를 넣습니다.
    """
    # # ✅ [디버깅] 파싱하기 직전의 문자열을 출력합니다.
    # print(f"[Serial Importer-DEBUG] Processing line: '{line.strip()}'")

    parsed_data = parse_sensor_data(line.strip())

    # ✅ [디버깅] 파싱 결과를 출력합니다. (이전과 동일)
    # print(f"[Serial Importer-DEBUG] Parsed data: {parsed_data}")

    if parsed_data:
        if global_queues.is_processing_active:
            await asyncio.gather(
                RAW_DATA_QUEUE.put(parsed_data),
                LOGGING_QUEUE.put(parsed_data)
            )
        # 디버깅 웹 UI에는 항상 데이터 전송
        await broadcast(json.dumps(asdict(parsed_data)), debug_websockets)


async def serial_data_importer():
    """
    [테스트용 워커] 시리얼 포트에서 데이터를 읽어 파이프라인에 주입합니다.
    """
    port = select_serial_port()
    if not port:
        return

    loop = asyncio.get_running_loop()
    # `to_thread`를 사용해 블로킹 함수를 별도 스레드에서 실행
    await asyncio.to_thread(blocking_serial_reader, port, loop)