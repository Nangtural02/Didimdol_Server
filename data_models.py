from dataclasses import dataclass, field
from typing import List, Dict, Any

# @dataclass
# class SensorData:
#     """
#     디버깅용 센서데이터
#     """
#     message: str
#     Timestamp: int

@dataclass
class SensorData:
    """
    앵커에서 서버로 들어오는 데이터 양식
    """
    Timestamp: float
    TagAddr: int
    Seq: int
    Distance: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    mx: float
    my: float
    mz: float

@dataclass
class SquatSegment:
    """
    segmenter -> inference로 보낼 데이터
    """
    repetition_count: int
    start_timestamp: float
    data: List[SensorData] = field(default_factory=list)

@dataclass
class InferenceResult:
    """
    inference -> 앱으로 보낼 결과 데이터
    """
    count: int
    head: int
    spine: int
    knees: int
    feet: int
    totalScore: int