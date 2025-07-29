# (★★★ 1번 조치) PyCharm이 끼어들기 전에 그래픽 엔진부터 바꿉니다.
# 이 코드는 반드시 다른 matplotlib import 보다 먼저 나와야 합니다.
import matplotlib

matplotlib.use('Agg')

import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.colors import ListedColormap
from collections import Counter
import io
import base64
import os


def setup_korean_font():
    """
    (★★★ 2번 조치) Matplotlib의 폰트 캐시를 강제로 다시 만들고, 나눔고딕을 전역 폰트로 설정합니다.
    """
    FONT_FILENAME = 'NanumGothic.ttf'

    if not os.path.exists(FONT_FILENAME):
        print(f"!!! 에러: 폰트 파일 '{FONT_FILENAME}'을 찾을 수 없습니다.")
        print("스크립트와 같은 폴더에 NanumGothic.ttf 파일을 넣어주세요.")
        return False

    # 기존 캐시를 무시하고 폰트 목록을 강제로 다시 스캔합니다.
    fm._load_fontmanager(try_read_cache=False)

    # 전역 폰트 설정
    plt.rc('font', family='NanumGothic')
    plt.rc('axes', unicode_minus=False)

    print("✓ 한글 폰트 설정이 완료되었습니다.")
    return True


# --- 이하 데이터 처리 및 시각화 로직은 이전과 거의 동일 ---
# (단, 폰트 관련 인자는 모두 제거되었습니다. 전역 설정이 알아서 처리합니다.)

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
    ax.set_title(f'혼동 행렬: {title}', fontsize=15)
    ax.set_xlabel('모델 예측', fontsize=12)
    ax.set_ylabel('실제 정답', fontsize=12)
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
    fig, ax = plt.subplots(figsize=(12, 4))
    cmap = ListedColormap(['#FF6B6B', '#6BFF6B'])
    sns.heatmap(pivot_df, cmap=cmap, linewidths=.5, linecolor='white', cbar=False, ax=ax, annot=False)
    ax.set_title('횟수별 정/오답 결과', fontsize=16)
    ax.set_xlabel('횟수', fontsize=12)
    ax.set_ylabel('부위', fontsize=12)
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    plt.close(fig)
    return f'data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode("utf-8")}'


def generate_html_report(human_csv_path, model_csv_path, output_html_path):
    try:
        human_df = pd.read_csv(human_csv_path, encoding='utf-8')
        model_df = pd.read_csv(model_csv_path, encoding='utf-8')
    except FileNotFoundError as e:
        print(f"오류: 파일을 찾을 수 없습니다 - {e.filename}")
        return

    # 데이터 처리
    ground_truth = {
        f'{part}{i}': TEXT_TO_CODE_FLAT.get(Counter(human_df[f'{part}{i}'].dropna()).most_common(1)[0][0], -1) for i in
        range(1, 16) for part in ['상체', '무릎', '발'] if
        f'{part}{i}' in human_df and not human_df[f'{part}{i}'].dropna().empty}
    model_col_map = {'head': '상체', 'knees': '무릎', 'feet': '발'}
    model_predictions = {f'{part_name}{row["count"]}': row[model_col] for _, row in model_df.iterrows() for
                         model_col, part_name in model_col_map.items() if model_col in row}
    results = [{'횟수': int(''.join(filter(str.isdigit, k))), '부위': ''.join(filter(str.isalpha, k)),
                '정답 (사람)': CODE_TO_LABEL[''.join(filter(str.isalpha, k))].get(v, 'N/A'),
                '예측 (모델)': CODE_TO_LABEL[''.join(filter(str.isalpha, k))].get(model_predictions.get(k), 'N/A'),
                '결과': '정답' if v == model_predictions.get(k) else '오답'} for k, v in ground_truth.items()]
    results_df = pd.DataFrame(results).sort_values(by=['횟수', '부위'])

    # 정확도 계산
    correct_count = len(results_df[results_df['결과'] == '정답'])
    total_count = len(results_df)
    accuracies = {'전체': (correct_count / total_count) if total_count > 0 else 0}
    for part in ['상체', '무릎', '발']:
        part_df = results_df[results_df['부위'] == part]
        accuracies[part] = accuracy_score(part_df['정답 (사람)'], part_df['예측 (모델)']) if not part_df.empty else 0

    # 시각화
    correctness_plot_img = plot_correctness_by_rep_to_base64(results_df)
    cm_images = {}
    for part in ['상체', '무릎', '발']:
        part_df = results_df[results_df['부위'] == part]
        if not part_df.empty:
            y_true_part = part_df['정답 (사람)'].map(TEXT_TO_CODE_FLAT).fillna(-1).astype(int)
            y_pred_part = part_df['예측 (모델)'].map(TEXT_TO_CODE_FLAT).fillna(-1).astype(int)
            cm_images[part] = plot_confusion_matrix_to_base64(y_true_part.tolist(), y_pred_part.tolist(),
                                                              list(LABEL_TO_CODE[part].keys()), part)

    # HTML 생성
    html = f"""
    <html><head><title>AI 모델 성능 평가 리포트</title>
        <style>
            body {{ font-family: 'Segoe UI', 'Malgun Gothic', sans-serif; margin: 20px; }} h1, h2 {{ color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
            table {{ border-collapse: collapse; width: 90%; max-width: 800px; margin-top: 20px; margin-bottom: 40px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }} th {{ background-color: #f2f2f2; }}
            .correct {{ color: #28a745; font-weight: bold; }} .incorrect {{ color: #dc3545; font-weight: bold; }}
            .summary-card {{ background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
            .plot-container {{ text-align: center; margin-bottom: 40px; }} .plot-container img {{ max-width: 100%; height: auto; border: 1px solid #ddd; }}
            .cm-container {{ display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; }}
        </style>
    </head><body>
        <h1>AI 모델 성능 평가 리포트</h1>
        <h2>1. 성능 요약</h2>
        <div class="summary-card">
            <p><strong>전체 정확도: {accuracies.get('전체', 0):.2%}</strong> ({correct_count} / {total_count})</p>
            <ul>
                <li>상체 정확도: {accuracies.get('상체', 0):.2%}</li>
                <li>무릎 정확도: {accuracies.get('무릎', 0):.2%}</li>
                <li>발 정확도: {accuracies.get('발', 0):.2%}</li>
            </ul>
        </div>
        <h2>2. 횟수별 정/오답 결과</h2>
        <div class="plot-container">
            <p>모델이 각 횟수별, 부위별로 예측을 올바르게 수행했는지 보여줍니다. (초록색: 정답, 빨간색: 오답)</p>
            <img src="{correctness_plot_img}" alt="횟수별 정/오답 결과">
        </div>
        <h2>3. 혼동 행렬 (Confusion Matrix)</h2>
        <p>모델이 어떤 유형의 실수를 하는지 보여줍니다. (Y축: 실제 정답, X축: 모델 예측)</p>
        <div class="cm-container">
            <div class="plot-container"><h3>상체</h3><img src="{cm_images.get('상체', '')}" alt="상체 혼동 행렬"></div>
            <div class="plot-container"><h3>무릎</h3><img src="{cm_images.get('무릎', '')}" alt="무릎 혼동 행렬"></div>
            <div class="plot-container"><h3>발</h3><img src="{cm_images.get('발', '')}" alt="발 혼동 행렬"></div>
        </div>
        <h2>4. 상세 비교 결과</h2>
        <table>
            <tr><th>횟수</th><th>부위</th><th>정답 (사람 Voted)</th><th>예측 (모델)</th><th>결과</th></tr>
            {''.join([f"<tr><td>{row['횟수']}</td><td>{row['부위']}</td><td>{row['정답 (사람)']}</td><td>{row['예측 (모델)']}</td><td class='{'correct' if row['결과'] == '정답' else 'incorrect'}'>{row['결과']}</td></tr>" for _, row in results_df.iterrows()])}
        </table>
    </body></html>
    """

    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"'{output_html_path}' 파일이 성공적으로 생성되었습니다.")

if __name__ == '__main__':
    # 1. 사람이 라벨링한 CSV 파일 경로
    HUMAN_LABELS_CSV = './analysis/googleForm/googleForm1.csv'

    # 2. 모델이 예측한 결과 CSV 파일 경로 (새로운 형식의 파일)
    MODEL_RESULTS_CSV = './analysis/modelOutput/modelOutput1.csv'

    # 3. 결과 리포트 파일 이름
    OUTPUT_HTML = './analysis/evaluation_report_1.html'

    # 리포트 생성 함수 호출
    generate_html_report(HUMAN_LABELS_CSV, MODEL_RESULTS_CSV, OUTPUT_HTML)