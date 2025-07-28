import asyncio
import torch
import torch.nn as nn
from torch.autograd import Function
import numpy as np
import pandas as pd
import os
from math import floor, ceil

from global_queues import SEGMENT_QUEUE, RESULT_QUEUE
from data_models import SquatSegment, InferenceResult, SensorData
from collections import defaultdict # 그룹화를 위해 import

# -------------------- GRL --------------------
class GradientReversalFunction(Function):
    @staticmethod
    def forward(ctx, input, lambd=1.0):
        ctx.lambd = lambd
        return input.view_as(input)
    @staticmethod
    def backward(ctx, grad_output):
        return grad_output.neg() * ctx.lambd, None

def grad_reverse(x, lambd=1.0):
    return GradientReversalFunction.apply(x, lambd)

# -------------------- 모델 --------------------
class MultiLSTMFeatureExtractor(nn.Module):
    def __init__(self, input_dim=50, hidden_dim=128, num_layers=3):
        super(MultiLSTMFeatureExtractor, self).__init__()
        self.lstms = nn.ModuleList([
            nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True, bidirectional=True)
            for _ in range(4)
        ])
        self.layernorms = nn.ModuleList([
            nn.LayerNorm(hidden_dim * 2) for _ in range(4)
        ])

    def forward(self, x):
        # x: (batch, seq_len=120, features=50)
        feats = []
        for i in range(4):
            _, (h_n, _) = self.lstms[i](x)
            feat = torch.cat((h_n[-2], h_n[-1]), dim=1)
            feat = self.layernorms[i](feat)
            feats.append(feat)  # (batch, hidden*2)
        return feats
# -------------------- Task Classifier --------------------
class TaskClassifier(nn.Module):
    def __init__(self, feat_dim=256, num_classes=3):
        super().__init__()
        # 기존 단일 FC → 두 개의 FC + Dropout
        self.net = nn.Sequential(
            nn.Linear(feat_dim, feat_dim//2),  # 256 → 128
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(feat_dim//2, num_classes)  # 128 → 3
        )

    def forward(self, feat):
        return self.net(feat)
    
# -------------------- Domain Classifier --------------------
class DomainClassifier(nn.Module):
    def __init__(self, feat_dim, num_domains):
        super(DomainClassifier, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(feat_dim * 4, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, num_domains)
        )

    def forward(self, feats):
        concat_feat = torch.cat(feats, dim=1)  # (batch, feat_dim*4)
        return self.net(concat_feat)

# -------------------- SquatPoseModel --------------------
class SquatPoseModel(nn.Module):
    def __init__(self, feat_dim=256, num_domains=6):
        super(SquatPoseModel, self).__init__()
        self.feature_extractors = MultiLSTMFeatureExtractor(input_dim=50, hidden_dim=feat_dim//2)
        self.task_heads = nn.ModuleList([
            TaskClassifier(feat_dim, 3) for _ in range(4)
        ])
        self.domain_classifier = DomainClassifier(feat_dim, num_domains)

    def forward(self, x, lambd=1.0):
        feats = self.feature_extractors(x)  # List of 4 (batch, feat_dim)
        task_outputs = [head(feats[i]) for i, head in enumerate(self.task_heads)]
        rev_feats = [grad_reverse(f, lambd) for f in feats]
        domain_output = self.domain_classifier(rev_feats)
        return task_outputs, domain_output

# -------------------- 모델 로드 및 초기 설정 --------------------
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
MODEL_PATH = "squat_model.pth"  # 훈련된 모델 파일

# 모델 인스턴스 생성 및 가중치 로드
try:
    print("[Inference] AI 모델 로딩을 시작합니다...")
    model = SquatPoseModel(feat_dim=256, num_domains=7)
    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.to(DEVICE)
    model.eval()  # 모델을 추론 모드로 설정
    print(f"[Inference] AI 모델 로딩 완료. 추론 장치: {DEVICE}")
except FileNotFoundError:
    print(f"[Inference] [ERROR] 모델 파일을 찾을 수 없습니다: {MODEL_PATH}")
    model = None
except Exception as e:
    print(f"[Inference] [ERROR] 모델 로딩 중 오류 발생: {e}")
    model = None

def preprocess_data(data_segment: list[SensorData]) -> torch.Tensor:
    """
    실시간으로 들어온 센서 데이터를 AI 모델 입력 형식에 맞게 전처리합니다.
    """
    if not data_segment:
        return torch.zeros(120, 50) # 데이터 없으면 빈 텐서 반환
    
    # 학습 시와 동일한 태그 순서 및 특징
    ANCHOR_IDS_IN_ORDER = [0, 1, 2, 3, 4]
    FEATURES_IN_ORDER = ['ax','ay','az','gx','gy','gz','mx','my','mz','Distance']

    # 50ms 간격으로 시간 축을 나누어 시퀀스 생성
    WINDOW_SIZE = 0.05

    # 1. 전체 데이터의 시작과 끝 시간 찾기
    start_time = min(d.Timestamp for d in data_segment)
    end_time = max(d.Timestamp for d in data_segment)
    duration = end_time - start_time

    # 2. 필요한 시간 창(bin)의 개수 계산
    num_bins = ceil(duration / WINDOW_SIZE) if duration > 0 else 1
    
    # 3. 각 시간 창을 대표하는 빈 딕셔너리 리스트 생성
    binned_data = [{} for _ in range(num_bins)]

    # 4. 모든 데이터를 타임스탬프에 따라 맞는 시간 창에 배치
    for d in data_segment:
        bin_index = floor((d.Timestamp - start_time) / WINDOW_SIZE)
        if bin_index >= num_bins: bin_index = num_bins - 1
        binned_data[bin_index][d.TagAddr] = d

    # 5. 시간 창 리스트를 순회하며 (N, 50) 형태의 시퀀스 생성 (누락 데이터는 np.nan 처리)
    processed_sequence = []
    for time_bin in binned_data:
        feature_vector_for_one_step = []
        
        for anchor_id in ANCHOR_IDS_IN_ORDER:
            sensor_data = time_bin.get(anchor_id) 
            
            if sensor_data:
                # 데이터가 있으면 특징 추출
                features = [
                    float(getattr(sensor_data, f)) for f in FEATURES_IN_ORDER
                ]
                feature_vector_for_one_step.extend(features)
            else:
                # 데이터가 없으면 np.nan으로 10개 채우기 (핵심 변경점)
                feature_vector_for_one_step.extend([np.nan] * len(FEATURES_IN_ORDER))
        
        processed_sequence.append(feature_vector_for_one_step)

    # 6. Pandas DataFrame을 이용한 선형 보간 (학습 코드와 동일)
    segment_array = np.array(processed_sequence, dtype=np.float32)
    df_segment = pd.DataFrame(segment_array)
    
    # 선형 보간
    df_interp = df_segment.interpolate(method='linear', axis=0, limit_direction='both')
    
    # 보간 후에도 남은 NaN은 0으로 채우기
    df_interp = df_interp.fillna(0)
    df_interp = df_interp.round(3)
    
    final_sequence = df_interp.values

    # 7. 최종 시퀀스를 (120, 50) 크기의 텐서로 변환 (패딩 및 자르기)
    x = torch.tensor(final_sequence, dtype=torch.float32)

    if x.shape[0] < 120:
        pad_zeros = torch.zeros(120 - x.shape[0], 50)
        x = torch.cat([x, pad_zeros], dim=0)
    
    x = x[:120, :]

    return x

async def run_ai_inference_placeholder(data_segment: list[SensorData]) -> InferenceResult:
    """
    AI 모델을 사용하여 스쿼트 자세를 추론합니다.
    """
    if model is None:
        print("[Inference] [ERROR] 모델이 로드되지 않아 추론을 건너뜁니다.")
        # 모델 로드 실패 시 기본값 또는 에러 상태를 반환할 수 있습니다.
        return InferenceResult(count=0, head=9, spine=9, knees=9, feet=9, totalScore=0)

    # 1. 데이터 전처리
    input_tensor = preprocess_data(data_segment)
    input_tensor = input_tensor.unsqueeze(0)
    input_tensor = input_tensor.to(DEVICE)

    # 2. 모델 추론 실행 (그래디언트 계산 비활성화)
    with torch.no_grad():
        # `asyncio.to_thread`를 사용하여 동기적인 모델 추론 코드가
        # 비동기 이벤트 루프를 막지 않도록 별도 스레드에서 실행합니다. (Python 3.9+).
        # Python 3.8 이하의 경우: loop.run_in_executor(None, model, input_tensor)
        task_preds, _ = await asyncio.to_thread(model, input_tensor, 1.0)

    # 3. 추론 결과 해석
    # 각 task_head의 출력 (logits)에서 가장 확률이 높은 클래스를 선택합니다.
    # head, spine, knees, feet 순서라고 가정합니다.
    pred_labels = [torch.argmax(pred, dim=1).item() for pred in task_preds]
    
    head_status, spine_status, knee_status, feet_status = pred_labels[0], pred_labels[1], pred_labels[2], pred_labels[3]

    # 4. 점수 계산 (훈련 코드의 점수 산정 방식과 동일하게 적용)
    total_status_sum = head_status + spine_status + knee_status + feet_status
    score = max(0, 100 - int(total_status_sum * (100 / 8))) # 예시 점수 계산

    return InferenceResult(
        count=0,
        head=head_status, spine=spine_status, knees=knee_status, feet=feet_status,
        totalScore=score
    )


async def inference_worker():
    """[파이프라인 3단계] SEGMENT_QUEUE에서 데이터를 꺼내 AI 추론하는 함수 호출 후,
     결과를 RESULT_QUEUE에 넣습니다."""

    print("[Inference] 추론 워커 시작됨.")
    while True:
        squat_event: SquatSegment = await SEGMENT_QUEUE.get()
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 시작...")

        result = await run_ai_inference_placeholder(squat_event.data)
        result.count = squat_event.repetition_count

        await RESULT_QUEUE.put(result)
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 완료. 결과를 RESULT_QUEUE에 추가함.")
        SEGMENT_QUEUE.task_done()