import asyncio
import torch
import numpy as np
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
            for _ in range(3)
        ])
        self.layernorms = nn.ModuleList([
            nn.LayerNorm(hidden_dim * 2) for _ in range(3)
        ])

    def forward(self, x):
        # x: (batch, seq_len=120, features=50)
        feats = []
        for i in range(3):
            _, (h_n, _) = self.lstms[i](x)
            feat = torch.cat((h_n[-2], h_n[-1]), dim=1)
            feat = self.layernorms[i](feat)
            feats.append(feat)  # (batch, hidden*2)
        return feats

# -------------------- Task Classifier --------------------
class TaskClassifier(nn.Module):
    def __init__(self, feat_dim=192, num_classes=3):
        super().__init__()
        # 기존 단일 FC → 두 개의 FC + Dropout
        self.net = nn.Sequential(
            nn.Linear(feat_dim, feat_dim//2), 
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(feat_dim//2, num_classes)  # 128 → 3
        )

    def forward(self, feat):
        return self.net(feat)

# -------------------- Domain Classifier --------------------
class DomainClassifier(nn.Module):
    def __init__(self, feat_dim, num_domains):
        super(DomainClassifier, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(feat_dim * 3, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Dropout(0.6),
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
            TaskClassifier(feat_dim, 3),
            TaskClassifier(feat_dim, 2),
            TaskClassifier(feat_dim, 2)
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
MODEL_PATH = "squat_model_best.pth"  # 훈련된 모델 파일

# 모델 인스턴스 생성 및 가중치 로드
try:
    print("[Inference] AI 모델 로딩을 시작합니다...")
    model = SquatPoseModel(feat_dim=192, num_domains=8)
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

# 1. 튜닝을 통해 찾은 최적의 임계값을 상수로 정의
OPTIMAL_THRESHOLDS = [
    {1: 0.8000000000000002, 2: 0.5000000000000001},  # Task 1
    {1: 0.1},  # Task 2
    {1: 0.9500000000000003},  # Task 3
]

# 2. 임계값을 적용하는 헬퍼 함수를 추론 코드에 추가
def apply_thresholds_live(probs, thresholds):
    num_samples = probs.shape[0]
    preds = np.zeros(num_samples, dtype=int)
    for i in range(num_samples):
        pass_class_1 = probs[i, 1] >= thresholds.get(1, 0.5)
        pass_class_2 = probs[i, 2] >= thresholds.get(2, 0.5)
        if pass_class_1 and pass_class_2:
            preds[i] = 1 if probs[i, 1] > probs[i, 2] else 2
        elif pass_class_1:
            preds[i] = 1
        elif pass_class_2:
            preds[i] = 2
    return preds

def preprocess_data(data_segment: list[SensorData]) -> torch.Tensor:
    """
    실시간으로 들어온 센서 데이터를 AI 모델 입력 형식에 맞게 전처리합니다.
    ('Seq' 순환 문제를 'Timestamp' 기준으로 정렬하여 해결)
    """
    if not data_segment:
        print("[Inference] [ERROR] 데이터 세그먼트가 비어 있습니다.")
        return torch.zeros(120, 50)

    # 학습 시와 동일한 태그 순서 및 특징
    ANCHOR_IDS_IN_ORDER = [0, 1, 2, 3, 4]
    FEATURES_IN_ORDER = ['ax','ay','az','gx','gy','gz','mx','my','mz','Distance']

    # 1. 'Seq' 번호를 기준으로 모든 데이터를 그룹화합니다.
    grouped_by_seq = defaultdict(list)
    for d in data_segment:
        grouped_by_seq[d.Seq].append(d)

    # 2. (핵심 수정) 각 'Seq' 그룹을 대표하는 최소 Timestamp를 찾아,
    #    Timestamp를 기준으로 그룹(Seq 번호)들을 정렬합니다.
    #    이렇게 하면 Seq가 256->1로 순환해도 시간 순서가 보장됩니다.
    
    # 각 Seq 그룹과 해당 그룹의 최소 타임스탬프를 튜플로 묶습니다: (seq, min_timestamp)
    seq_with_timestamp = []
    for seq, data_list in grouped_by_seq.items():
        min_timestamp = min(d.Timestamp for d in data_list)
        seq_with_timestamp.append((seq, min_timestamp))
        
    # 타임스탬프를 기준으로 정렬합니다.
    seq_with_timestamp.sort(key=lambda x: x[1])
    
    # 정렬된 순서대로 Seq 번호만 다시 추출합니다.
    sorted_seqs = [seq for seq, _ in seq_with_timestamp]

    # 3. 정렬된 'Seq'를 순회하며 (N, 50) 형태의 시퀀스를 생성합니다.
    processed_sequence = []
    for seq in sorted_seqs:
        feature_vector_for_one_step = []
        data_in_seq = {d.TagAddr: d for d in grouped_by_seq[seq]}

        for anchor_id in ANCHOR_IDS_IN_ORDER:
            sensor_data = data_in_seq.get(anchor_id)
            
            if sensor_data:
                features = [float(getattr(sensor_data, f)) for f in FEATURES_IN_ORDER]
                feature_vector_for_one_step.extend(features)
            else:
                feature_vector_for_one_step.extend([np.nan] * len(FEATURES_IN_ORDER))
        
        processed_sequence.append(feature_vector_for_one_step)

    if not processed_sequence:
        print("[Inference] [WARN] 처리 후 시퀀스가 비어 있습니다.")
        return torch.zeros(120, 50)

    # 4. (기존과 동일) Pandas DataFrame을 이용한 선형 보간
    segment_array = np.array(processed_sequence, dtype=np.float32)
    df_segment = pd.DataFrame(segment_array)
    df_interp = df_segment.interpolate(method='linear', axis=0, limit_direction='both').fillna(0).round(3)
    final_sequence = df_interp.values

    # 5. (기존과 동일) 최종 시퀀스를 (120, 50) 크기의 텐서로 변환
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
        return InferenceResult(count=0, head=9, knees=9, feet=9, totalScore=0)

    # 1. 데이터 전처리
    input_tensor = preprocess_data(data_segment)
    input_tensor = input_tensor.unsqueeze(0)
    input_tensor = input_tensor.to(DEVICE)

    # 2. 모델 추론 실행 (그래디언트 계산 비활성화)
    with torch.no_grad():
        task_preds, _ = await asyncio.to_thread(model, input_tensor, 1.0)

    # 3. 추론 결과 해석
    final_labels = []
    # for i, pred_logits in enumerate(task_preds):
    #     print(f"Task {i} pred_logits device: {pred_logits.device}")
    #     # 확률로 변환
    #     probabilities = torch.softmax(pred_logits, dim=1).cpu().detach().numpy()
    #     # 해당 태스크의 최적 임계값 적용
    #     pred_label = apply_thresholds_live(probabilities, OPTIMAL_THRESHOLDS[i])
    #     final_labels.append(pred_label.item()) # .item()으로 스칼라 값 추출

    final_labels = [torch.argmax(pred, dim=1).item() for pred in task_preds]

    head_status, knee_status, feet_status = final_labels[0], final_labels[1], final_labels[2]

    # 4. 점수 계산 (훈련 코드의 점수 산정 방식과 동일하게 적용)
    total_status_sum = head_status + knee_status + feet_status
    score = max(0, 100 - int(total_status_sum * (100 / 8))) # 예시 점수 계산

    return InferenceResult(
        count=0,
        head=head_status, knees=knee_status, feet=feet_status,
        totalScore=score
    )


async def inference_worker():
    """[파이프라인 3단계] SEGMENT_QUEUE에서 데이터를 꺼내 AI 추론하는 함수 호출 후,
     결과를 RESULT_QUEUE에 넣습니다."""

    print("[Inference] 추론 워커 시작됨.")
    while True:
        squat_event: SquatSegment = await SEGMENT_QUEUE.get()
        print(f"[Inference] Segmented data lentgh: {len(squat_event.data)}")
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 시작...")

        result = await run_ai_inference_placeholder(squat_event.data)
        result.count = squat_event.repetition_count

        await RESULT_QUEUE.put(result)
        print(f"[Inference] {squat_event.repetition_count}번째 동작 추론 완료. 결과를 RESULT_QUEUE에 추가함.")
        SEGMENT_QUEUE.task_done()