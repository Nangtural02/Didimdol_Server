import asyncio
from data_models import SensorData

# def parse_sensor_data(s: str) -> SensorData | None:
#     """ 디버깅용 파서 """
#     if not s or not s.strip(): return None
#     original_message = s
#     if s.startswith('{') and s.endswith('}'): s = s[1:-1]
#     p = [x.strip() for x in s.split(",")]
#     if len(p) != 2:
#         print(f"[Parser] Invalid packet length: {len(p)}. Content: '{original_message}'")
#         return None
#     try:
#         return SensorData(
#             p[0], int(p[1])
#         )
#     except (ValueError, IndexError) as e:
#         print(f"[Parser] Error parsing packet: {e}. Content: '{original_message}'")
#         return None

def parse_sensor_data(s: str) -> SensorData | None:
    """수신된 문자열을 파싱하여 SensorData 객체를 반환합니다."""
    if not s or not s.strip(): return None
    original_message = s
    if s.startswith('{') and s.endswith('}'): s = s[1:-1]
    p = [x.strip() for x in s.split(",")]
    if len(p) != 12:
        print(f"[Parser] Invalid packet format. Length: {len(p)}, Content: '{original_message}'")
        return None
    try:
        return SensorData(
            Timestamp=asyncio.get_running_loop().time(),
            TagAddr=int(p[0]), Seq=int(p[1]), Distance=float(p[2]),
            ax=float(p[3]), ay=float(p[4]), az=float(p[5]),
            gx=float(p[6]), gy=float(p[7]), gz=float(p[8]),
            mx=float(p[9]), my=float(p[10]), mz=float(p[11])
        )
    except (ValueError, IndexError) as e:
        print(f"[Parser] Error parsing packet: {e}. Content: '{original_message}'")
        return None

async def clear_all_queues(*queues: asyncio.Queue):
    """
    주어진 모든 asyncio.Queue의 내용을 비웁니다.
    """
    print("[Queue Utils] 모든 데이터 큐를 초기화합니다...")
    cleared_count = 0
    for q in queues:
        while not q.empty():
            try:
                item = q.get_nowait()
                cleared_count += 1
                q.task_done()
            except asyncio.QueueEmpty:
                # 다른 작업이 동시에 아이템을 가져간 경우
                break
    if cleared_count > 0:
        print(f"[Queue Utils] 총 {cleared_count}개의 잔여 아이템을 제거했습니다.")