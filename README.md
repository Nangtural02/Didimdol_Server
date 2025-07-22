# 디딤돌 서버
## 코드 구성
- 크게 4개 파일에 있는 스레드가 비동기로 파이프라인을 구성합니다.
  - 메인 스레드: 데이터 수신
  - logger.py : raw 데이터 로깅
  - segmenter.py : raw 데이터를 횟수에 맞게 잘라주는 역할
  - inference.py : segmenter.py에서 잘라준 데이터와 메타데이터로 추론하는 역할
  - result_to_app.py (emitter): inference.py에서 처리한 결과를 앱으로 전송
- 각각의 스레드 간에는 Queue를 활용해서, 비동기로 처리합니다. 
  - RAW_DATA_QUEUE : 메인 스레드 -> segmenter.py
  - LOGGING_QUEUE : 메인 스레드 -> logger.py
  - SEGMENT_QUEUE: segmenter.py -> inference.py
  - RESULT_QUEUE: inference.py -> result_to_app.py
- 각 큐 간에 사용하는 데이터 형식은 data_models.py에 정의되어 있습니다. 

## 사용법
- \[서버ip주소\]:5050으로 접속하여 실시간 RAW 데이터를 볼 수 있습니다
- 앱을 활용해 (운동 시작)/ (운동 종료) 명령을 내려야 서버가 동작하고, 그 외에는 앵커에서  받은 데이터를 모두 버립니다.