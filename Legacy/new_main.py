from quart import Quart, websocket
from collections import defaultdict
import json
import asyncio
import os
from datetime import datetime

app = Quart(__name__)

# ------------------------------------------------------------------------------
# 1. 전역 데이터 구조
# ------------------------------------------------------------------------------
# 블록을 blockNum 기준으로 묶어서 저장
# 예: blocks_data[blockNum] = {
#     "tag_1": [...],
#     "tag_2": [...],
#     ...
# }
blocks_data = defaultdict(dict)

# 각 태그별 마지막 blockNumber
last_block_number = dict()

# 로그 파일 경로(동적으로 생성)
log_file_path = None

# 주기적으로(5초마다) 블록 단위로 파일에 저장
LOG_SAVE_INTERVAL = 5

# 한 블록이 '완성'되었다고 볼 때 필요한 태그
REQUIRED_TAGS = {"tag_1", "tag_2", "tag_3", "tag_4", "tag_5"}

# ----------------------------------------------------------------------------
# 2. 유틸 함수
# ----------------------------------------------------------------------------
def initialize_log_file():
    """
    현재 시간을 기준으로 로그 파일 이름을 생성하고 경로를 설정합니다.
    """
    global log_file_path
    now = datetime.now()
    log_file_path = f"Log_{now.strftime('%m_%d_%H_%M_%S')}.json"
    print(f"Log file initialized: {log_file_path}")


def parse_sensor_data(data):
    """
    JSON 데이터를 파싱하여 blockNumber와 센서 데이터를 반환.

    예시 입력: {"blockNum": 205, "imuData": "[[...], [...], ...]"}
    출력: (205, [[...], [...], ...])
    """
    try:
        #print("Try to Parse data:", data)
        if not isinstance(data, dict):
            raise ValueError("수신된 데이터가 JSON 형식의 딕셔너리가 아닙니다.")

        block_number = data.get("blockNum")
        imu_data = data.get("imuData")

        if block_number is None or imu_data is None:
            raise ValueError("blockNum 또는 imuData 키가 데이터에 포함되어 있지 않습니다.")
        if not isinstance(block_number, int):
            raise ValueError(f"blockNum 값이 유효하지 않습니다: {block_number}")

        # blockNum이 0~65535 범위라 가정 (그 이상이면 오류 처리)
        if block_number < 0 or block_number > 65535:
            raise ValueError(f"blockNum 범위를 벗어남: {block_number}")

        sensor_data = json.loads(imu_data)
        return block_number, sensor_data

    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"데이터 파싱에 실패했습니다: {e}")


def check_block_number_sequence(tag_name, current_bn):
    """
    - 각 태그별로 이전 blockNumber를 조회해 다음 blockNumber가 맞는지 검증.
    - 이전 blockNum + 1 == 현재 blockNum 이어야 한다. (wrap-around 고려 안 함)
    - 불연속이면 ValueError
    """
    prev_bn = last_block_number.get(tag_name, None)
    if prev_bn is None:
        # 아직 첫 블록이 들어온 적이 없으면 바로 등록
        last_block_number[tag_name] = current_bn
        return

    expected_next_bn = prev_bn + 1  # wrap-around 미고려

    if current_bn != expected_next_bn:
        raise ValueError(
            f"[{tag_name}] Invalid blockNum sequence. "
            f"Expected {expected_next_bn}, but got {current_bn}."
        )

    # 정상적이면 갱신
    last_block_number[tag_name] = current_bn


def store_data(block_number, tag_name, sensor_data):
    """
    수신한 태그 데이터를 blocks_data에 저장
    (실제 파일 저장은 save_logs_periodically()에서 주기적으로 수행)
    """
    blocks_data[block_number][tag_name] = sensor_data


async def save_logs_periodically():
    """
    5초마다 blocks_data를 확인하여,
    - 모든 태그가 모인 블록(complete block)을 파일에 기록
    - 기록한 블록은 blocks_data에서 제거
    """
    while True:
        try:
            # 이번 라운드에 파일로 저장할 블록 모음
            blocks_to_save = []

            # dict 순회 시 변경 방지를 위해 list(...)로 복사
            for bn, tag_dict in list(blocks_data.items()):
                current_tags = set(tag_dict.keys())
                # REQUIRED_TAGS(예: 5개 태그) 모두 도착했는지 확인
                if REQUIRED_TAGS.issubset(current_tags):
                    # 완성된 블록 -> 로그로 저장
                    block_info = {
                        "blockNumber": bn,
                        "allTagsData": tag_dict
                    }
                    blocks_to_save.append(block_info)

                    # 저장된 블록은 blocks_data에서 제거
                    del blocks_data[bn]

            # 실제로 파일에 쓰기
            if blocks_to_save and log_file_path:
                if os.path.exists(log_file_path):
                    with open(log_file_path, 'r+', encoding='utf-8') as f:
                        try:
                            existing_logs = json.load(f)
                            if not isinstance(existing_logs, list):
                                # 파일 내용이 리스트가 아니면 새로 만듦
                                existing_logs = []
                        except json.JSONDecodeError:
                            existing_logs = []

                        existing_logs.extend(blocks_to_save)
                        f.seek(0)
                        json.dump(existing_logs, f, ensure_ascii=False, indent=2)
                else:
                    # 파일이 없다면 새로 생성
                    with open(log_file_path, 'w', encoding='utf-8') as f:
                        json.dump(blocks_to_save, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"Error during periodic save: {e}")

        # 5초 대기 후 다시 실행
        await asyncio.sleep(LOG_SAVE_INTERVAL)


# ----------------------------------------------------------------------------
# 3. 라우트 및 WebSocket 핸들러
# ----------------------------------------------------------------------------
@app.route('/')
async def index():
    return 'Quart WebSocket Server is running'


async def handle_tag(tag_name):
    global log_file_path

    if log_file_path is None:
        initialize_log_file()

    while True:
        try:
            data = await websocket.receive_json()
            block_number, sensor_data = parse_sensor_data(data)

            # blockNum 불연속 여부 검증
            check_block_number_sequence(tag_name, block_number)

            # 저장
            store_data(block_number, tag_name, sensor_data)

            print(f"[{tag_name}] block_number={block_number}, sensor_data={sensor_data}")

        except ValueError as ve:
            # Invalid blockNum sequence -> 건너뛴 블록 제거
            if "Invalid blockNum sequence" in str(ve):
                print(f"Skip block for {tag_name}: {ve}")
                # 혹시 이미 저장된 데이터가 있으면 제거
                if block_number in blocks_data:
                    del blocks_data[block_number]
                # 연결은 종료하지 않고 다음 메시지 처리
                continue
            else:
                print(f"Error on WebSocket {tag_name}: {ve}")
                break

        except Exception as e:
            print(f"Error on WebSocket {tag_name}: {e}")
            break


@app.websocket('/tag_1')
async def handle_tag_1():
    # 새 연결 시점에 tag_1 관련 전역 변수 초기화
    last_block_number['tag_1'] = None
    return await handle_tag('tag_1')


@app.websocket('/tag_2')
async def handle_tag_2():
    last_block_number['tag_2'] = None
    return await handle_tag('tag_2')


@app.websocket('/tag_3')
async def handle_tag_3():
    last_block_number['tag_3'] = None
    return await handle_tag('tag_3')


@app.websocket('/tag_4')
async def handle_tag_4():
    last_block_number['tag_4'] = None
    return await handle_tag('tag_4')


@app.websocket('/tag_5')
async def handle_tag_5():
    last_block_number['tag_5'] = None
    return await handle_tag('tag_5')


# ----------------------------------------------------------------------------
# 4. 서버 실행 전 백그라운드 태스크 등록
# ----------------------------------------------------------------------------
@app.before_serving
async def startup():
    app.add_background_task(save_logs_periodically)

# ----------------------------------------------------------------------------
# 5. 실행부
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='202.30.29.212', port=5000, debug=True)
