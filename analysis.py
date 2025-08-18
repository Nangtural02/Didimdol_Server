import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from collections import Counter
import io
import base64
import os
import glob
import re

plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

LABEL_TO_CODE = {
    '상체': {'자연스러움': 0, '상체가 앞으로 쏠림(기울어짐)': 1, '상체 뒤로 쏠림(뒤로 넘어질 뻔함)': 2},
    '무릎': {'발과 무릎이 잘 정렬됨': 0, '무릎이 발끝에 비해 너무 앞으로 나옴': 1, '무릎이 발에 비해 너무 벌어짐': 2},
    '발': {'발바닥이 평평하게 유지, 안쪽으로 말리거나 들리지 않음': 0, '발바다의 과도한 내반(안쪽으로 말림) 또는 외반(바깥쪽으로 굽음)': 1, '발뒤꿈치 혹은 앞쪽이 지면에서 들림': 2}
}
TEXT_TO_CODE_FLAT = {**LABEL_TO_CODE['상체'], **LABEL_TO_CODE['무릎'], **LABEL_TO_CODE['발'],
                     '발바닥의 과도한 내반(안쪽으로 말림) 또는 외반(바깥쪽으로 굽음)': 1}
CODE_TO_LABEL = {
    '상체': {v: k for k, v in LABEL_TO_CODE['상체'].items()}, '무릎': {v: k for k, v in LABEL_TO_CODE['무릎'].items()},
    '발': {v: k for k, v in LABEL_TO_CODE['발'].items()}
}


def plot_confusion_matrix_to_base64(y_true, y_pred, labels, title):
    if not y_true or not y_pred: return ""
    cm = confusion_matrix(y_true, y_pred, labels=range(len(labels)))
    fig, ax = plt.subplots(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', xticklabels=labels, yticklabels=labels, ax=ax)
    ax.set_title(title, fontsize=15)
    ax.set_xlabel('모델 예측', fontsize=12)
    ax.set_ylabel('사람 정답', fontsize=12)
    ax.tick_params(axis='x', rotation=45)
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    plt.close(fig)
    return f'data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode("utf-8")}'


def plot_correctness_by_rep_to_base64(results_df):
    if results_df.empty: return ""
    df = results_df.copy()
    df['정답여부'] = (df['결과'] == '정답').astype(int)
    pivot_df = df.pivot_table(index='부위', columns='횟수', values='정답여부').reindex(['상체', '무릎', '발'])
    fig, ax = plt.subplots(figsize=(max(12, len(pivot_df.columns) * 0.6), 4))
    cmap = ListedColormap(['#FF6B6B', '#6BFF6B'])
    sns.heatmap(pivot_df, cmap=cmap, linewidths=.5, linecolor='white', cbar=False, ax=ax, annot=False)
    ax.set_title('횟수별 정/오답 결과', fontsize=16)
    ax.set_xlabel('횟수', fontsize=12)
    ax.set_ylabel('부위', fontsize=12)
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    plt.close(fig)
    return f'data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode("utf-8")}'


def generate_html_report(exerciser_name, results_df, accuracies, rater_names, output_html_path):
    rater_a_name, rater_b_name = rater_names
    correctness_plot_img = plot_correctness_by_rep_to_base64(results_df)
    cm_images = {}
    for part in ['상체', '무릎', '발']:
        part_df = results_df[results_df['부위'] == part]
        if not part_df.empty:
            labels = list(LABEL_TO_CODE[part].keys())
            cm_images[f'{part}_A'] = plot_confusion_matrix_to_base64(
                part_df[f'응답_{rater_a_name}_코드'].tolist(), part_df['모델_코드'].tolist(), labels,
                f'혼동 행렬: 모델 vs {rater_a_name} ({part})')
            cm_images[f'{part}_B'] = plot_confusion_matrix_to_base64(
                part_df[f'응답_{rater_b_name}_코드'].tolist(), part_df['모델_코드'].tolist(), labels,
                f'혼동 행렬: 모델 vs {rater_b_name} ({part})')

    html = f"""
    <!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>AI 모델 성능 평가 리포트: {exerciser_name}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Noto Sans KR', sans-serif; margin: 40px; }} h1, h2, h3 {{ color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; font-weight: 700; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0 40px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: center; }} th {{ background-color: #f2f2f2; }}
        .correct {{ color: #28a745; font-weight: bold; }} .incorrect {{ color: #dc3545; font-weight: bold; }}
        .summary-card {{ background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
        .plot-container {{ text-align: center; margin-bottom: 40px; }} .plot-container img {{ max-width: 100%; border: 1px solid #ddd; }}
        .cm-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }}
    </style></head><body>
    <h1>AI 모델 성능 평가 리포트 ({exerciser_name})</h1><h2>1. 성능 요약</h2><div class="summary-card">
    <p><strong>전체 정확도: {accuracies.get('전체', 0):.2%}</strong> ({accuracies.get('correct_count', 0)} / {accuracies.get('total_count', 0)})</p>
    <ul><li>상체 정확도: {accuracies.get('상체', 0):.2%}</li><li>무릎 정확도: {accuracies.get('무릎', 0):.2%}</li><li>발 정확도: {accuracies.get('발', 0):.2%}</li></ul></div>
    <h2>2. 횟수별 정/오답 결과 (두 평가자 중 하나라도 일치하면 정답)</h2>
    <div class="plot-container"><img src="{correctness_plot_img}" alt="횟수별 정/오답 결과"></div>
    <h2>3. 혼동 행렬 (모델 vs 평가자)</h2><div class="cm-grid">
    <div class="plot-container"><h3>모델 vs {rater_a_name}</h3>
    <img src="{cm_images.get('상체_A', '')}" alt="상체 혼동 행렬 A"><img src="{cm_images.get('무릎_A', '')}" alt="무릎 혼동 행렬 A"><img src="{cm_images.get('발_A', '')}" alt="발 혼동 행렬 A"></div>
    <div class="plot-container"><h3>모델 vs {rater_b_name}</h3>
    <img src="{cm_images.get('상체_B', '')}" alt="상체 혼동 행렬 B"><img src="{cm_images.get('무릎_B', '')}" alt="무릎 혼동 행렬 B"><img src="{cm_images.get('발_B', '')}" alt="발 혼동 행렬 B"></div></div>
    <h2>4. 상세 비교 결과</h2><table><tr><th>횟수</th><th>부위</th><th>{rater_a_name}</th><th>{rater_b_name}</th><th>모델 예측</th><th>최종 결과</th></tr>
    {''.join([f"<tr><td>{row['횟수']}</td><td>{row['부위']}</td><td>{row[f'응답_{rater_a_name}']}</td><td>{row[f'응답_{rater_b_name}']}</td><td>{row['모델_라벨']}</td><td class='{'correct' if row['결과'] == '정답' else 'incorrect'}'>{row['결과']}</td></tr>" for _, row in results_df.iterrows()])}
    </table></body></html>"""
    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ '{output_html_path}' 파일이 성공적으로 생성되었습니다.")


def process_single_exerciser(exerciser_name, model_df_raw, human_df_raw):
    print(f"\n---\n📊 '{exerciser_name}' 운동자 데이터 처리 중...")
    exerciser_col = '운동자 이름을 적어주세요'
    rater_col = '응답자 이름을 적어주세요'
    human_df_exerciser = human_df_raw[human_df_raw[exerciser_col] == exerciser_name].copy()
    rater_names = human_df_exerciser[rater_col].unique()
    if len(rater_names) != 2:
        print(f"⚠️ 경고: '{exerciser_name}' 운동자의 평가자가 {len(rater_names)}명입니다. 2명이 아니므로 건너뜁니다.")
        return

    rater_a_name, rater_b_name = rater_names
    print(f"👥 평가자: {rater_a_name}, {rater_b_name}")

    data_frames = []
    for rater in rater_names:
        df = human_df_exerciser[human_df_exerciser[rater_col] == rater]
        df_melted = df.melt(id_vars=[rater_col], value_vars=[c for c in df.columns if re.match(r'^(상체|무릎|발)\d+$', c)],
                            var_name='부위_횟수', value_name=f'응답_{rater}')
        df_melted[['부위', '횟수']] = df_melted['부위_횟수'].str.extract(r'([가-힣]+)(\d+)')
        df_melted['횟수'] = pd.to_numeric(df_melted['횟수'])
        df_melted[f'응답_{rater}_코드'] = df_melted[f'응답_{rater}'].map(TEXT_TO_CODE_FLAT)
        data_frames.append(df_melted[['횟수', '부위', f'응답_{rater}', f'응답_{rater}_코드']])

    model_df = model_df_raw.copy()
    model_df = model_df.rename(columns={'count': '횟수', 'head': '상체', 'knees': '무릎', 'feet': '발'})
    model_melted = model_df.melt(id_vars=['횟수'], value_vars=['상체', '무릎', '발'], var_name='부위', value_name='모델_코드')
    results_df = pd.merge(data_frames[0], data_frames[1], on=['횟수', '부위'])
    results_df = pd.merge(results_df, model_melted, on=['횟수', '부위'])
    results_df['모델_라벨'] = results_df.apply(lambda row: CODE_TO_LABEL[row['부위']].get(row['모델_코드'], 'N/A'), axis=1)
    results_df['결과'] = results_df.apply(lambda row: '정답' if (row['모델_코드'] == row[f'응답_{rater_a_name}_코드']) or (
                row['모델_코드'] == row[f'응답_{rater_b_name}_코드']) else '오답', axis=1)

    total_count = len(results_df)
    correct_count = len(results_df[results_df['결과'] == '정답'])
    accuracies = {'전체': (correct_count / total_count) if total_count > 0 else 0, 'correct_count': correct_count,
                  'total_count': total_count}
    for part in ['상체', '무릎', '발']:
        part_df = results_df[results_df['부위'] == part]
        accuracies[part] = accuracy_score((part_df['결과'] == '정답'), [True] * len(part_df)) if not part_df.empty else 0

    session_timestamp = re.search(r'(\d{8}_\d{6})', model_df_raw.name).group(1)
    output_dir = 'output'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"report_{exerciser_name}_{session_timestamp}.html")
    generate_html_report(exerciser_name, results_df, accuracies, rater_names, output_path)


def find_and_process_files(model_dir, human_dir):
    """(★★★ 수정) 최신 구글폼 1개와 log 폴더의 모든 모델 파일을 '시간순'으로 매칭하여 분석"""
    human_files = glob.glob(os.path.join(human_dir, 'didim - *.csv'))
    if not human_files:
        print(f"❌ 오류: '{human_dir}' 폴더에 구글폼 응답 파일('didim - *.csv')이 없습니다.")
        return
    latest_human_file = max(human_files, key=os.path.getmtime)
    print(f"📂 기준 구글폼 파일: '{os.path.basename(latest_human_file)}'")

    model_files = sorted(glob.glob(os.path.join(model_dir, 'model_test_session_*.csv')))
    if not model_files:
        print(f"❌ 오류: '{model_dir}' 폴더에 모델 결과 파일('model_test_session_*.csv')이 없습니다.")
        return
    print(f"📂 분석할 모델 로그 파일 {len(model_files)}개를 찾았습니다.")

    try:
        human_df_raw = pd.read_csv(latest_human_file, encoding='utf-8')
        human_df_raw.name = latest_human_file
    except Exception as e:
        print(f"❌ 오류: '{latest_human_file}' 파일 읽기 실패 - {e}")
        return

    exerciser_col = '운동자 이름을 적어주세요'
    timestamp_col = '타임스탬프'
    if exerciser_col not in human_df_raw.columns or timestamp_col not in human_df_raw.columns:
        print(f"❌ 오류: 구글폼 파일에 '{exerciser_col}' 또는 '{timestamp_col}' 컬럼이 없습니다.")
        return

    # 타임스탬프를 datetime 객체로 변환하여 정렬 가능하게 만듦
    human_df_raw[timestamp_col] = pd.to_datetime(human_df_raw[timestamp_col], format='%Y. %m. %d. %p %I:%M:%S',
                                                 errors='coerce')
    human_df_sorted = human_df_raw.sort_values(by=timestamp_col)

    # 정렬된 상태에서 중복을 제거하여 시간순 운동자 목록 생성
    exerciser_names_in_order = human_df_sorted[exerciser_col].unique().tolist()

    if len(model_files) != len(exerciser_names_in_order):
        print("\n⚠️ 경고: 모델 로그 파일의 수와 구글폼의 운동자 수가 일치하지 않습니다!")
        print(f"   - 모델 로그 파일: {len(model_files)}개")
        print(f"   - 운동자 (시간순): {len(exerciser_names_in_order)}명 ({', '.join(exerciser_names_in_order)})")

    print("\n--- 파일 매칭 결과 ---")
    for i, model_path in enumerate(model_files):
        if i < len(exerciser_names_in_order):
            exerciser_name = exerciser_names_in_order[i]
            print(f"  - 모델 로그: {os.path.basename(model_path)} <--> 운동자: {exerciser_name}")
            try:
                model_df_raw = pd.read_csv(model_path, encoding='utf-8')
                model_df_raw.name = model_path
                process_single_exerciser(exerciser_name, model_df_raw, human_df_raw)
            except Exception as e:
                print(f"❌ 오류: '{model_path}' 파일 처리 중 문제 발생 - {e}")
        else:
            print(f"  - 모델 로그: {os.path.basename(model_path)} <--> ⚠️ 매칭할 운동자 없음")


if __name__ == '__main__':
    MODEL_LOG_DIR = 'log'
    HUMAN_LABEL_DIR = 'analysis/googleForm'

    find_and_process_files(MODEL_LOG_DIR, HUMAN_LABEL_DIR)