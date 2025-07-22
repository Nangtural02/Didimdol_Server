# tcp_ack_echo_server.py
import socket

HOST = "0.0.0.0"      # 모든 인터페이스에서 수신
PORT = 5050           # 필요하면 변경
BUF  = 256           # 수신 버퍼 크기

PREFIX = b"RECEIVED: "  # 앞에 붙일 확인 메시지

def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen()
        print(f"[ACK-ECHO] listening on {HOST}:{PORT}")

        while True:
            conn, addr = srv.accept()
            print(f"[ACK-ECHO] connection from {addr}")
            with conn:
                while True:
                    data = conn.recv(BUF)
                    if not data:             # 연결 종료
                        break
                    print("[ACK-ECHO] received:", data.decode("utf-8"))
                    conn.sendall(PREFIX + data)   # 확인문구 + 원본 그대로
            print(f"[ACK-ECHO] {addr} closed")

if __name__ == "__main__":
    main()
