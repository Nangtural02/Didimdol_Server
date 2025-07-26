# from __future__ import annotations
# import asyncio, collections, math, time
# from typing import Deque, Dict, List
#
# import numpy as np
# import pandas as pd
# from scipy.signal import butter, filtfilt
#
# # 외부 모듈
# from global_queues import RAW_DATA_QUEUE, SEGMENT_QUEUE
# from data_models   import SensorData, SquatSegment
#
# # ────────── 고정 파라미터 (기존 값 유지) ──────────
# PRR_WINDOW_N   = 10
# PRR_THRESHOLD  = 0.80
#
# UWB_OUT_THR    = 1.0
# BUFFER_N       = 9
# REG_WIN_N      = 9
#
# IMU_SMOOTH_WIN = 5
# SEG_WIN_N      = 5
# SEG_STEP       = SEG_WIN_N // 2
# SEG_THR_ACC    = 500
# SEG_THR_GYRO   = 3000
#
# ALPHA_IMU      = 1.0
# BETA_UWB       = 0.6
# UWB_Z_DENOM    = 3.0
# FUSED_THR      = 0.6
#
# EXCLUDE_TAGS   : Dict[int, None] = {}
# MIN_VALID_LEN  = 50          # 태그 신뢰성 최소 샘플
#
# BATCH_SIZE     = 100         # RAW_DATA_QUEUE 에서 꺼낼 배치 크기
# MAX_BUF_SEC    = 30          # deque 타임라인 최대 보존(초) – 필요시 조정
# # ──────────────────────────────────────────────
#
#
# # ────────────── UWB 보조 클래스 ──────────────
# class PRRGate:
#     """패킷 수신율 계산용 슬라이딩 창"""
#     def __init__(self, win=PRR_WINDOW_N):
#         self.w: Deque[bool] = collections.deque(maxlen=win)
#         self.prev: int | None = None
#         self.valid = False
#
#     def update(self, seq: int) -> bool:
#         if self.prev is None:
#             self.w.append(True)
#         else:
#             gap = (seq - self.prev) % 256
#             self.w.extend([False] * max(gap - 1, 0))
#             self.w.append(True)
#         self.prev = seq
#         if len(self.w) == self.w.maxlen and (sum(self.w) / len(self.w)) >= PRR_THRESHOLD:
#             self.valid = True
#         return self.valid
#
#
# class UWBMeanFilter:
#     """거리 스파이크 제거 + 이동평균 버퍼"""
#     def __init__(self, N=BUFFER_N, thr=UWB_OUT_THR):
#         self.buf: Deque[float] = collections.deque(maxlen=N)
#         self.thr = thr
#
#     def add(self, d: float) -> float | None:
#         if len(self.buf) < self.buf.maxlen:
#             self.buf.append(d)
#             return None
#         mu = float(np.mean(self.buf))
#         if abs(d - mu) > self.thr:
#             return mu          # 스파이크 → 보정값 반환
#         self.buf.append(d)
#         return d               # 정상 수치 반환
#
#
# # ────────────── IMU 필터 함수 ──────────────
# def robust_filter(arr: np.ndarray,
#                   fs: float,
#                   fc: float = 20.0,
#                   order: int = 4,
#                   sigma: float = 3.0,
#                   iters: int = 2) -> np.ndarray:
#     """Butterworth 저역통과 + 반복적 3σ 보간 필터"""
#     nyq = 0.5 * fs
#     fc  = min(fc, 0.99 * nyq)
#     b, a = butter(order, fc / nyq, 'low', analog=False)
#     out = arr.astype(np.float32)
#
#     for _ in range(iters):
#         out = filtfilt(b, a, out, axis=0)
#         m, s = np.nanmean(out, 0), np.nanstd(out, 0)
#         for c in range(out.shape[1]):
#             bad = np.abs(out[:, c] - m[c]) > sigma * s[c]
#             if bad.any():
#                 idx  = np.arange(len(out))
#                 good = ~bad
#                 out[bad, c] = np.interp(idx[bad], idx[good], out[good, c])
#     return out
#
#
# def moving_avg(v: np.ndarray, n: int = IMU_SMOOTH_WIN) -> np.ndarray:
#     """1D 또는 2D 슬라이딩 평균"""
#     if v.ndim == 1:
#         return np.convolve(v, np.ones(n) / n, 'same')
#     return np.apply_along_axis(lambda x: np.convolve(x, np.ones(n) / n, 'same'), 0, v)
#
#
# # ────────────── 태그별 상태 ──────────────
# class TagState:
#     def __init__(self):
#         self.prr_gate    = PRRGate()
#         self.uwb_filter  = UWBMeanFilter()
#         # (시간, imu(6), dist) 슬라이딩 버퍼
#         self.t:    Deque[float]        = collections.deque()
#         self.imu:  Deque[np.ndarray]   = collections.deque()
#         self.dist: Deque[float]        = collections.deque()
#         self.sigma: float | None       = None
#
#     # 버퍼 오래된 데이터 버리기
#     def _truncate_old(self, now_ts: float):
#         while self.t and (now_ts - self.t[0]) > MAX_BUF_SEC:
#             self.t.popleft()
#             self.imu.popleft()
#             self.dist.popleft()
#
#     # 새 샘플 통합
#     def ingest(self, data: SensorData) -> bool:
#         if not self.prr_gate.update(data.Seq):
#             return False
#         d_clean = self.uwb_filter.add(data.Distance)
#         if d_clean is None:
#             return False
#
#         self.t.append(data.Timestamp)
#         self.imu.append(np.array([data.ax, data.ay, data.az,
#                                   data.gx, data.gy, data.gz,
#                                   data.mx, data.my, data.mz],
#                                  dtype=np.float32))
#         self.dist.append(d_clean)
#         self._truncate_old(data.Timestamp)
#
#         # MAD(σ) 갱신
#         if len(self.dist) >= MIN_VALID_LEN:
#             arr = np.fromiter(self.dist, float)
#             med = np.nanmedian(arr)
#             mad = 1.4826 * np.nanmedian(abs(arr - med))
#             self.sigma = mad if mad > 1e-6 else None
#         return True  # 데이터 유효
#
#
# # ────────────── 온라인 처리기 ──────────────
# class OnlineProcessor:
#     def __init__(self):
#         self.tags: Dict[int, TagState] = {}
#         self.prev_cp: float | None = None
#         self.raw_cps: List[float]  = []
#
#     # ─── 배치 처리 ───
#     def process_batch(self, batch: List[SensorData]) -> List[SquatSegment]:
#         """100개 배치 수신 → 태그 상태 누적 → 세그먼트 계산"""
#         segments: List[SquatSegment] = []
#
#         # 1) 태그별 상태 갱신
#         for s in batch:
#             if s.TagAddr in EXCLUDE_TAGS:
#                 continue
#             st = self.tags.setdefault(s.TagAddr, TagState())
#             st.ingest(s)
#
#         # 2) 신뢰 가능한 태그 판별
#         reliable = {tag for tag, st in self.tags.items()
#                     if len(st.t) >= MIN_VALID_LEN and st.sigma is not None}
#         if not reliable:
#             return segments  # 데이터 부족
#
#         # 3) 메인 시간축 선택
#         main_tag = max(reliable, key=lambda k: len(self.tags[k].t))
#         t_common = np.array(self.tags[main_tag].t, dtype=float)
#         L = len(t_common)
#         if L < max(2 * SEG_WIN_N, REG_WIN_N):
#             return segments  # 윈도우 부족
#
#         # 4) 태그별 시간축 보간 + 점수 시계열 계산
#         tag_scores_mat: List[np.ndarray] = []
#         for tag in reliable:
#             st = self.tags[tag]
#             t_arr = np.array(st.t, dtype=float)
#             imu_arr = np.vstack(st.imu)           # (N,6)
#             dist_arr = np.fromiter(st.dist, float)
#
#             # 시간축 보간
#             imu_interp = np.stack([np.interp(t_common, t_arr, imu_arr[:, j],
#                                              left=np.nan, right=np.nan)
#                                    for j in range(6)], axis=1)
#             dist_interp = np.interp(t_common, t_arr, dist_arr,
#                                     left=np.nan, right=np.nan)
#
#             # 직전 robust+moving_avg 적용
#             fs_est = 1.0 / np.median(np.diff(t_arr)) if len(t_arr) > 3 else 50.0
#             imu_filt = robust_filter(imu_interp, fs_est)
#             imu_smooth = moving_avg(moving_avg(imu_filt))
#
#             # 슬라이딩 윈도우 점수
#             imu_score_ts = np.zeros(L, dtype=float)
#             uwb_score_ts = np.zeros(L, dtype=float)
#
#             softmax = lambda x: np.exp(x - np.max(x)) / (np.sum(np.exp(x - np.max(x))) + 1e-6)
#
#             for i in range(max(2 * SEG_WIN_N, REG_WIN_N), L, SEG_STEP):
#                 # IMU --------------------------
#                 acc = imu_smooth[:, :3]
#                 gyro = imu_smooth[:, 3:]
#
#                 prev_acc = acc[i - 2 * SEG_WIN_N:i - SEG_WIN_N].mean(0)
#                 curr_acc = acc[i - SEG_WIN_N:i].mean(0)
#                 slope_acc = abs(curr_acc - prev_acc)
#                 accel_score = float((slope_acc > SEG_THR_ACC).dot(softmax(slope_acc)))
#
#                 prev_gyro = gyro[i - 2 * SEG_WIN_N:i - SEG_WIN_N].mean(0)
#                 curr_gyro = gyro[i - SEG_WIN_N:i].mean(0)
#                 slope_gyro = abs(curr_gyro - prev_gyro)
#                 gyro_score = float((slope_gyro > SEG_THR_GYRO).dot(softmax(slope_gyro)))
#
#                 imu_score_ts[i] = accel_score + gyro_score
#
#                 # UWB -------------------------
#                 if np.isnan(dist_interp[i - REG_WIN_N:i]).all():
#                     uwb_score_ts[i] = 0.0
#                 else:
#                     y = dist_interp[i - REG_WIN_N:i]
#                     x = np.arange(REG_WIN_N)
#                     mask = ~np.isnan(y)
#                     if mask.sum() < REG_WIN_N // 2:
#                         uwb_score_ts[i] = 0.0
#                     else:
#                         a, b = np.polyfit(x[mask], y[mask], 1)
#                         y_pred = a * x[-1] + b
#                         resid = abs(dist_interp[i - 1] - y_pred)
#                         z = resid / (st.sigma + 1e-6)
#                         uwb_score_ts[i] = min(1.0, z / UWB_Z_DENOM)
#
#             fused = (ALPHA_IMU * imu_score_ts + BETA_UWB * uwb_score_ts) / (ALPHA_IMU + BETA_UWB)
#             tag_scores_mat.append(fused)
#
#         fused_mean = np.nanmean(np.vstack(tag_scores_mat), axis=0)
#
#         # 5) 분할 포인트 추출 + 1초 중복 제거
#         for i in range(max(2 * SEG_WIN_N, REG_WIN_N), L, SEG_STEP):
#             if fused_mean[i] >= FUSED_THR:
#                 cp_time = t_common[i]
#                 if (self.prev_cp is None) or ((cp_time - self.prev_cp) > 1.0):
#                     self.raw_cps.append(cp_time)
#                     self.prev_cp = cp_time
#
#         # 6) 활성 구간(pair) → SquatSegment 생성
#         while len(self.raw_cps) >= 2:
#             start, end = self.raw_cps[0], self.raw_cps[1]
#             if end <= start:      # 논리 오류 방지
#                 self.raw_cps.pop(0); continue
#             segments.append(SquatSegment(start_time=start, end_time=end))
#             # 다음 pair로 이동
#             self.raw_cps = self.raw_cps[2:]
#
#         return segments
#
#
# # ────────────── 메인 코루틴 ──────────────
# # … (위쪽 동일) …
#
# async def repetition_segmenter():
#     print("[Segmenter] 동작 분할기 시작됨.")
#     proc = OnlineProcessor()
#
#     rep_count = 0                               # ←★ ① 총 스쿼트 카운터
#
#     while True:
#         batch: List[SensorData] = [await RAW_DATA_QUEUE.get() for _ in range(BATCH_SIZE)]
#         segments = proc.process_batch(batch)
#
#         for seg in segments:
#
#             rep_count += 1                      # ←★ ② 번호 증가
#             print(f"[Segmenter] ✅ 스쿼트 #{rep_count} 종료시각: {seg.end_time:.2f}s "
#                   f"(시작 {seg.start_time:.2f}s, 지속 {seg.end_time - seg.start_time:.2f}s)")
#             await SEGMENT_QUEUE.put(seg)        # (필요하면 그대로 큐에 전파)
#
#
#
# # ────────────── 실행 진입점 (로컬 테스트용) ──────────────
# if __name__ == "__main__":
#     async def _debug_producer():
#         """임시 모의 데이터 공급: 50 Hz × 2 태그, 20초"""
#         import random
#         fs = 50.0
#         Ts = 1.0 / fs
#         t0 = time.time()
#
#         for i in range(int(20 * fs)):
#             t = time.time() - t0
#             # 두 태그 샘플 생성
#             for tag in (1, 2):
#                 RAW_DATA_QUEUE.put_nowait(SensorData(
#                     Timestamp=t,
#                     TagAddr=tag,
#                     Seq=i % 256,
#                     Distance=2.0 + 0.1 * math.sin(0.5 * t) + random.uniform(-0.01, 0.01),
#                     ax=random.randint(-400, 400),
#                     ay=random.randint(-400, 400),
#                     az=random.randint(-400, 400),
#                     gx=random.randint(-2500, 2500),
#                     gy=random.randint(-2500, 2500),
#                     gz=random.randint(-2500, 2500),
#                     mx=random.randint(-100, 100),
#                     my=random.randint(-100, 100),
#                     mz=random.randint(-100, 100),
#
#                 ))
#             await asyncio.sleep(Ts)
#
#     async def _consumer():
#         while True:
#             seg: SquatSegment = await SEGMENT_QUEUE.get()
#             print(f"[Consumer] ➡️  {seg}")
#
#     async def main():
#         await asyncio.gather(
#             _debug_producer(),           # ← 실제 환경에서는 제거/교체
#             repetition_segmenter(),
#             _consumer(),
#         )
#
#     asyncio.run(main())
# segmenter.py
import asyncio
import time
import global_queues
from global_queues import RAW_DATA_QUEUE, SEGMENT_QUEUE
from data_models import SensorData, SquatSegment


async def repetition_segmenter():
    """[파이프라인 2단계] 100개 데이터를 묶어 SquatSegment 객체로 만들어 SEGMENT_QUEUE에 넣습니다."""
    print("[Segmenter] 동작 분할기 시작됨.")

    while True:
        segment_data: list[SensorData] = []

        first_data_point = await RAW_DATA_QUEUE.get()
        segment_data.append(first_data_point)
        start_time = first_data_point.Timestamp
        RAW_DATA_QUEUE.task_done()

        #todo: 분할 로직
        #지금은 그냥 100개 되면 잘라서 넘기는걸로 되어있습니다.
        for _ in range(99):
            data_point = await RAW_DATA_QUEUE.get()
            segment_data.append(data_point)
            RAW_DATA_QUEUE.task_done()

        global_queues.repetition_count += 1
        squat_event = SquatSegment(
            repetition_count=global_queues.repetition_count,
            start_timestamp=start_time,
            data=segment_data
        )

        await SEGMENT_QUEUE.put(squat_event)
        print(f"[Segmenter] {global_queues.repetition_count}번째 동작(SquatSegment)을 SEGMENT_QUEUE에 추가함.")