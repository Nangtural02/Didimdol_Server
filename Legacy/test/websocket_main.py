# websocket_echo_server.py
import asyncio
import websockets

HOST = "0.0.0.0"  # 모든 인터페이스에서 수신하려면 "0.0.0.0" 사용 가능
PORT = 5050
PREFIX = "RECEIVED: "  # 앞에 붙일 확인 메시지 (문자열로 변경)

# 각 클라이언트 연결을 처리하는 비동기 함수
async def echo(websocket):
    print(f"[WS-ECHO] connection from {websocket.remote_address}")
    try:
        # 클라이언트로부터 메시지가 올 때까지 비동기적으로 기다림
        async for message in websocket:
            print(f"[WS-ECHO] received: {message}")
            # 확인 문구와 함께 받은 메시지를 다시 보냄
            # await websocket.send(f"{PREFIX}{message}")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"[WS-ECHO] a client disconnected: {e}")
    finally:
        print(f"[WS-ECHO] {websocket.remote_address} closed")

# 서버를 시작하는 메인 비동기 함수
async def main():
    # 지정된 호스트와 포트에서 웹소켓 서버를 실행
    async with websockets.serve(echo, HOST, PORT, ping_interval=None, ping_timeout=10):
        print(f"[WS-ECHO] listening on ws://{HOST}:{PORT}")
        # 서버가 계속 실행되도록 무한히 대기
        await asyncio.Future()

if __name__ == "__main__":
    # 비동기 이벤트 루프를 실행하여 main 함수를 구동
    asyncio.run(main())