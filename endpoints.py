import asyncio
import json
import csv
import datetime as dt
from pathlib import Path
from dataclasses import asdict
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from quart import Blueprint, websocket, render_template, request
from utils import parse_sensor_data, clear_all_queues
import global_queues
from global_queues import LOGGING_QUEUE, RAW_DATA_QUEUE, sensor_websockets, app_websockets, debug_websockets
from data_models import SensorData, SquatSegment, InferenceResult

bp = Blueprint('main_endpoints', __name__)

try:
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    g_client = gspread.authorize(creds)
    print("[gspread] 구글 시트 클라이언트 초기화 성공.")
except Exception as e:
    g_client = None
    print(f"[gspread] 구글 시트 클라이언트 초기화 실패: {e}")

# 자세 부위 매핑 (기존과 동일)
POSE_PARTS_MAP = {"상체": "head", "무릎": "knees", "발": "feet"}

# 구글 설문지의 실제 답변 텍스트와 정확히 일치해야 합니다.
SCORE_MAP = {
    # 상체
    "자연스러움": 0,
    "상체가 앞으로 쏠림(기울어짐)": 1,
    "상체 뒤로 쏠림(뒤로 넘어질 뻔함)": 2,
    # 무릎
    "발과 무릎이 잘 정렬됨": 0,
    "무릎이 발끝에 비해 너무 앞으로 나옴": 1,
    "무릎이 발에 비해 너무 벌어짐": 2,
    # 발
    "발바닥이 평평하게 유지, 안쪽으로 말리거나 들리지 않음": 0,
    "발바닥의 과도한 내반(안쪽으로 말림) 또는 외반(바깥쪽으로 굽음)": 1,
    "발뒤꿈치 혹은 앞쪽이 지면에서 들림": 2
}

def process_data_for_analysis(selected_file_paths, selected_respondent_names, all_respondent_df):
    """
    [진짜 최종 버전] .str accessor 오류를 근본적으로 해결한 완벽한 분석 로직입니다.
    """
    if not selected_file_paths or not selected_respondent_names:
        return [], [], {}

    # --- 1. 모델 데이터 처리 ---
    all_model_dfs = [pd.read_csv(filepath) for filepath in selected_file_paths]
    model_df = pd.concat(all_model_dfs, ignore_index=True)
    pose_columns = list(POSE_PARTS_MAP.values())
    count_column_name = 'count'
    melted_model_df = model_df.melt(
        id_vars=[count_column_name], value_vars=pose_columns,
        var_name='Part_Eng', value_name='모델 예측'
    )
    melted_model_df = melted_model_df.groupby([count_column_name, 'Part_Eng']).first().reset_index()
    melted_model_df['모델 예측'] = pd.to_numeric(melted_model_df['모델 예측'], errors='coerce')

    # --- 2. 응답자 데이터 처리 ---
    pivoted_resp_df = pd.DataFrame()
    if not all_respondent_df.empty and selected_respondent_names:
        name_col = all_respondent_df.columns[1]
        filtered_resp_df = all_respondent_df[all_respondent_df[name_col].isin(selected_respondent_names)].copy()
        if not filtered_resp_df.empty:
            value_vars = [col for col in filtered_resp_df.columns if col.startswith('rep')]
            long_form_resp_df = filtered_resp_df.melt(
                id_vars=[all_respondent_df.columns[0], name_col], value_vars=value_vars,
                var_name='rep_part', value_name='score_text'
            )
            
            # --- [오류 해결 최종 로직] ---
            # .str 명령어를 사용하기 전에, .astype(str)을 사용하여 열 전체를 안전하게 문자열로 변환합니다.
            # 이렇게 하면 빈칸(None)도 문자열 'None'으로 바뀌어 오류가 발생하지 않습니다.
            long_form_resp_df['score'] = long_form_resp_df['score_text'].astype(str).str.strip().map(SCORE_MAP)
            # ---------------------------
            
            long_form_resp_df[count_column_name] = long_form_resp_df['rep_part'].str.extract(r'rep(\d+)').astype(int)
            long_form_resp_df['Part_Kor'] = long_form_resp_df['rep_part'].str.split('_').str[1]
            long_form_resp_df['Part_Eng'] = long_form_resp_df['Part_Kor'].map(POSE_PARTS_MAP)
            pivoted_resp_df = long_form_resp_df.pivot_table(
                index=[count_column_name, 'Part_Eng'], columns=name_col,
                values='score', aggfunc='first'
            ).reset_index()
            for name in selected_respondent_names:
                if name in pivoted_resp_df.columns:
                    pivoted_resp_df[name] = pd.to_numeric(pivoted_resp_df[name], errors='coerce')

    # --- 3. 완벽한 뼈대 생성 및 데이터 병합 ---
    reps = range(1, 31)
    parts = list(POSE_PARTS_MAP.values())
    mi = pd.MultiIndex.from_product([reps, parts], names=[count_column_name, 'Part_Eng'])
    base_df = mi.to_frame(index=False)
    merged_df = pd.merge(base_df, melted_model_df, on=[count_column_name, 'Part_Eng'], how='left')
    if not pivoted_resp_df.empty:
        merged_df = pd.merge(merged_df, pivoted_resp_df, on=[count_column_name, 'Part_Eng'], how='left')

    # --- 4. 최종 데이터 정리 및 정확도 계산 ---
    reverse_pose_map = {v: k for k, v in POSE_PARTS_MAP.items()}
    merged_df['자세 부위'] = merged_df['Part_Eng'].map(reverse_pose_map)
    accuracies = {}

    for name in selected_respondent_names:
        if name not in merged_df.columns: merged_df[name] = pd.NA
        model_col_numeric = pd.to_numeric(merged_df['모델 예측'], errors='coerce')
        respondent_col_numeric = pd.to_numeric(merged_df[name], errors='coerce')
        correct_predictions = (model_col_numeric == respondent_col_numeric)
        total_valid = correct_predictions.count()
        accuracies[name] = f"{(correct_predictions.sum() / total_valid) * 100:.1f}%" if total_valid > 0 else "N/A"
        merged_df[f'{name}_정확도'] = correct_predictions.map({True: 'O', False: 'X'})
        merged_df.loc[model_col_numeric.isna() | respondent_col_numeric.isna(), f'{name}_정확도'] = 'N/A'

    # --- 5. 최종 표시를 위한 데이터 타입 변환 ---
    merged_df = merged_df.fillna('N/A')
    display_columns = [count_column_name, '모델 예측'] + selected_respondent_names
    for col in display_columns:
        if col in merged_df.columns:
            merged_df[col] = merged_df[col].apply(lambda x: int(x) if pd.notna(x) and isinstance(x, (int, float)) and float(x).is_integer() else x)
    
    final_columns = [count_column_name, '자세 부위', '모델 예측']
    for name in selected_respondent_names:
        final_columns.extend([name, f'{name}_정확도'])
    merged_df = merged_df[final_columns]
    merged_df.rename(columns={'count': '스쿼트 횟수'}, inplace=True)
    
    return merged_df.to_dict('records'), selected_respondent_names, accuracies

@bp.route('/analysis', methods=['GET', 'POST'])
async def analysis():
    """
    [요청사항 반영] 이 페이지에 접속할 때마다 CSV와 구글 시트 데이터를 새로 불러옵니다.
    """
    # --- 1. CSV 파일 목록 로드 (매번 새로고침) ---
    result_files = []
    log_dir = Path("./log")
    if log_dir.exists():
        result_files = sorted([str(f) for f in log_dir.iterdir() if f.name.endswith("_results.csv")])

    # --- 2. 구글 시트 데이터 로드 (매번 새로고침) ---
    unique_respondent_names = []
    all_respondent_df = pd.DataFrame()
    if g_client:
        try:
            SHEET_NAME = "didim"
            worksheet = g_client.open(SHEET_NAME).worksheet("didim2")
            gsheet_data = worksheet.get_all_values()
            if len(gsheet_data) > 1:
                all_respondent_df = pd.DataFrame(gsheet_data[1:], columns=gsheet_data[0])
                name_col = all_respondent_df.columns[1]
                unique_respondent_names = sorted(all_respondent_df[name_col].unique().tolist())
        except Exception as e:
            print(f"구글 시트 데이터 처리 오류: {e}")
            unique_respondent_names = ["(시트 로드 실패)"]

    # --- 3. POST 요청 처리 ---
    analysis_results, respondent_headers, accuracies = [], [], {}
    selected_files, selected_respondents = [], []
    if request.method == 'POST':
        form_data = await request.form
        selected_files = form_data.getlist('result_files')
        selected_respondents = form_data.getlist('respondents')
        if selected_files and selected_respondents:
            analysis_results, respondent_headers, accuracies = process_data_for_analysis(
                selected_files, selected_respondents, all_respondent_df
            )

    return await render_template(
        'analysis.html',
        result_files=result_files,
        respondents=unique_respondent_names,
        analysis_results=analysis_results,
        respondent_headers=respondent_headers,
        accuracies=accuracies,
        selected_files=selected_files,
        selected_respondents=selected_respondents
    )

HISTORY_DIR = "./history"

def get_json_files():
    if not Path(HISTORY_DIR).exists():
        Path(HISTORY_DIR).mkdir()
        return []
    return sorted([f for f in Path(HISTORY_DIR).iterdir() if f.name.endswith(".json")])

async def broadcast(data: str, clients: set):
    tasks = [client.send(data) for client in clients]
    await asyncio.gather(*tasks, return_exceptions=True)


# ✅ [새 기능] 모델 테스트 UI를 제공하는 HTTP 엔드포인트
@bp.route('/model-test')
async def model_test_page():
    return await render_template("model_test.html")


@bp.websocket('/model-test-ws')
async def model_test_ws_handler():
    """웹 UI로부터 모델 테스트 제어 명령을 수신하고, 타이머를 관리하며, 결과를 반환합니다."""
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

                log_dir = Path("./log");
                log_dir.mkdir(exist_ok=True)
                now_str = dt.datetime.now().strftime("%Y%m%d_%HM%S")
                base_filename = f"model_test_session_{now_str}"
                global_queues.csv_file_path = log_dir / f"{base_filename}_results.csv"
                global_queues.csv_file_handler = global_queues.csv_file_path.open("w", encoding="utf-8", newline="")
                fieldnames = [field.name for field in InferenceResult.__dataclass_fields__.values()]
                global_queues.csv_writer = csv.DictWriter(global_queues.csv_file_handler, fieldnames=fieldnames)
                global_queues.csv_writer.writeheader()

                print(f"Model test started. Saving results to: {global_queues.csv_file_path}")
                await client.send(json.dumps({"status": "Overall test started"}))

            elif cmd == "stop_overall_test":
                if not global_queues.is_processing_active: continue
                global_queues.is_processing_active = False
                if global_queues.csv_file_handler:
                    global_queues.csv_file_handler.close()
                    print(f"CSV log saved to: {global_queues.csv_file_path}")
                global_queues.csv_file_handler, global_queues.csv_writer, global_queues.csv_file_path = None, None, None
                await client.send(json.dumps({"status": "Overall test stopped"}))

            # --- 자동 타이머 기반의 1회 스쿼트 측정 ---
            elif cmd == "start_timed_rep":
                if not global_queues.is_processing_active:
                    await client.send(json.dumps({"status": "Error", "message": "Overall test not started."}))
                    continue

                start_time = asyncio.get_running_loop().time()
                global_queues.rep_data_buffer.clear()

                global_queues.is_rep_recording_active = True
                await client.send(json.dumps({"status": "START", "message": "준비하세요..."}))

                await asyncio.sleep(2.0)
                down_time = asyncio.get_running_loop().time()
                await client.send(json.dumps({"status": "DOWN", "message": "내려가세요"}))

                await asyncio.sleep(2.0)
                await client.send(json.dumps({"status": "UP", "message": "올라오세요"}))

                await asyncio.sleep(2.0)
                global_queues.is_rep_recording_active = False
                stop_time = asyncio.get_running_loop().time()

                model_input_data = [dp for dp in global_queues.rep_data_buffer if down_time <= dp.Timestamp]

                response_payload = {}
                if model_input_data:
                    global_queues.repetition_count += 1
                    squat_event = SquatSegment(
                        repetition_count=global_queues.repetition_count,
                        start_timestamp=model_input_data[0].Timestamp,
                        data=model_input_data
                    )
                    await global_queues.SEGMENT_QUEUE.put(squat_event)

                    print("[Model Test] AI 추론 결과 신호를 기다리는 중...")
                    try:
                        await asyncio.wait_for(global_queues.NEW_RESULT_EVENT.wait(), timeout=5.0)
                        result = global_queues.last_inference_result
                        response_payload = {
                            "status": "STOP", "message": "측정 완료!",
                            "rep_count": global_queues.repetition_count,
                            "result": asdict(result) if result else None
                        }
                    except asyncio.TimeoutError:
                        print("[Model Test] AI 결과 수신 타임아웃.")
                        response_payload = {"status": "Error", "message": "AI 결과 타임아웃"}
                    finally:
                        global_queues.NEW_RESULT_EVENT.clear()
                else:
                    response_payload = {"status": "STOP", "message": "수집된 데이터가 없습니다.",
                                        "rep_count": global_queues.repetition_count}

                await client.send(json.dumps(response_payload))
                global_queues.rep_data_buffer.clear()

    except asyncio.CancelledError:
        print("[Model Test WS] 제어용 웹 UI 연결 끊김.")
    finally:
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