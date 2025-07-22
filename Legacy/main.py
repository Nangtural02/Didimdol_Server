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
# 블록을 (cycle, blockNum) 기준으로 묶어서 저장
# 예: blocks_data[(cycle, blockNum)] = {
#     "tag_1": [...],
#     "tag_2": [...],
#     ...
# }
blocks_data = defaultdict(dict)

# 각 태그별 마지막 blockNumber와 현재 cycle
last_block_number = dict()
block_cycle = defaultdict(int)

# 로그 파일 경로(동적으로 생성)
log_file_path = None

# 주기적으로(5초마다) 블록 단위로 파일에 저장
LOG_SAVE_INTERVAL = 5

# 한 블록이 '완성'되었다고 볼 때 필요한 태그
REQUIRED_TAGS = {"tag_1", "tag_2", "tag_3", "tag_4", "tag_5"}

#REQUIRED_TAGS = {"tag_1", "tag_2"} #Test용

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

        sensor_data = json.loads(imu_data)
        return block_number, sensor_data

    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"데이터 파싱에 실패했습니다: {e}")


def get_global_key(tag_name, block_number):
    """
    - 각 태그별로 이전 blockNumber를 조회해 다음 blockNumber가 맞는지 검증.
    - 255 -> 0인 경우에만 cycle을 +1.
    - 그 외에는 prev_blockNum + 1 == current_blockNum 이어야 함.
    """
    prev_bn = last_block_number.get(tag_name, None)

    # 아직 첫 블록이 들어온 적이 없는 태그라면 그대로 등록
    if prev_bn is None:
        last_block_number[tag_name] = block_number
        return (block_cycle[tag_name], block_number)

    expected_next_bn = (prev_bn + 1) % 256

    if block_number == expected_next_bn:
        # 255에서 0으로 넘어가는 상황이면 사이클 증가
        if prev_bn == 255 and block_number == 0:
            block_cycle[tag_name] += 1
    else:
        # 예외 상황: 블록이 건너뛰어짐
        raise ValueError(
            f"[{tag_name}] Invalid blockNum sequence. "
            f"Expected {expected_next_bn}, but got {block_number}."
        )

    # block_number 갱신
    last_block_number[tag_name] = block_number
    return (block_cycle[tag_name], block_number)


def store_data(cycle, bn, tag_name, sensor_data):
    """
    수신한 태그 데이터를 blocks_data에 부분 저장합니다.
    (실제 파일 저장은 save_logs_periodically()에서 주기적으로 수행)
    """
    # 해당 블록(cycle, bn) 딕셔너리에 현재 태그 데이터를 추가
    blocks_data[(cycle, bn)][tag_name] = sensor_data


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

            # blocks_data는 (cycle, bn)을 key로, 태그별 데이터를 저장
            # dict 순회 시 변경 방지를 위해 list(...)로 복사
            for (cycle, bn), tag_dict in list(blocks_data.items()):
                current_tags = set(tag_dict.keys())
                # REQUIRED_TAGS(예: 5개 태그) 모두 도착했는지 확인
                if REQUIRED_TAGS.issubset(current_tags):
                    # 완성된 블록 -> 로그로 저장
                    block_info = {
                        "cycle": cycle,
                        "blockNumber": bn,
                        "allTagsData": tag_dict
                    }
                    blocks_to_save.append(block_info)
                    # blocks_data에서 제거(이미 로그화)
                    del blocks_data[(cycle, bn)]

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

    # 만약 로그 파일이 아직 초기화되지 않았다면 초기화
    if log_file_path is None:
        initialize_log_file()

    while True:
        try:
            data = await websocket.receive_json()
            block_number, sensor_data = parse_sensor_data(data)

            # get_global_key()에서 예외가 나면 => "블록 건너뛰기" 처리
            skip_cycle = block_cycle[tag_name]  # 현재 태그 cycle(아직 업데이트 전)

            # 정상이면 cycle, bn이 반환
            cycle, bn = get_global_key(tag_name, block_number)
            store_data(cycle, bn, tag_name, sensor_data)

            print(f"[{tag_name}] cycle={cycle}, block_number={bn}, sensor_data={sensor_data}")

        except ValueError as ve:
            # 여기서 "Invalid blockNum sequence"가 나오면
            # => 이 block_number(= 건너뛴) 데이터는 완전히 버린다.
            # 이미 다른 태그가 같은 (cycle, block_number)에 저장했을 수도 있으므로 제거
            if "Invalid blockNum sequence" in str(ve):
                print(f"Skip block for {tag_name}: {ve}")
                # skip_cycle로 (skip_cycle, block_number)를 지워버림
                # 혹시나 blocks_data에 있으면 제거
                if (skip_cycle, block_number) in blocks_data:
                    del blocks_data[(skip_cycle, block_number)]
                # 연결은 끊지 않고 다음 메시지 처리
                continue
            else:
                # 그 외 ValueError면 일단 연결 끊고 로그
                print(f"Error on WebSocket {tag_name}: {ve}")
                break

        except Exception as e:
            print(f"Error on WebSocket {tag_name}: {e}")
            # WebSocket 에러 발생 시 반복문 탈출로 연결 종료
            break


@app.websocket('/tag_1')
async def handle_tag_1():
    # --- 새 연결 시점에 tag_1 관련 전역 변수 초기화 ---
    last_block_number['tag_1'] = None
    block_cycle['tag_1'] = 0
    global log_file_path
    log_file_path = None

    return await handle_tag('tag_1')


@app.websocket('/tag_2')
async def handle_tag_2():
    last_block_number['tag_2'] = None
    block_cycle['tag_2'] = 0
    return await handle_tag('tag_2')


@app.websocket('/tag_3')
async def handle_tag_3():
    last_block_number['tag_3'] = None
    block_cycle['tag_3'] = 0
    return await handle_tag('tag_3')


@app.websocket('/tag_4')
async def handle_tag_4():
    last_block_number['tag_4'] = None
    block_cycle['tag_4'] = 0
    return await handle_tag('tag_4')


@app.websocket('/tag_5')
async def handle_tag_5():
    last_block_number['tag_5'] = None
    block_cycle['tag_5'] = 0
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
