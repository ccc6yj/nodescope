"""极简 SOCKS5 CONNECT 服务器，仅用于端到端测试。

用法: python3 tests/socks5_server.py <port>
"""
import socket
import socketserver
import sys
import threading


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        s.recv(256)  # greeting
        s.sendall(b"\x05\x00")  # no auth
        data = s.recv(512)  # request
        if len(data) < 10 or data[1] != 1:
            s.close()
            return

        # 解析目标地址
        atyp = data[3]
        if atyp == 1:
            addr = socket.inet_ntoa(data[4:8])
            port = int.from_bytes(data[8:10], "big")
            reply = b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00"
        elif atyp == 3:
            n = data[4]
            addr = data[5:5 + n].decode()
            port = int.from_bytes(data[5 + n:7 + n], "big")
            reply = b"\x05\x00\x00\x03" + bytes([n]) + data[5:5 + n] + b"\x00\x00"
        elif atyp == 4:
            addr = socket.inet_ntop(socket.AF_INET6, data[4:20])
            port = int.from_bytes(data[20:22], "big")
            reply = b"\x05\x00\x00\x04" + b"\x00" * 16 + b"\x00\x00"
        else:
            s.close()
            return

        s.sendall(reply)
        try:
            remote = socket.create_connection((addr, port), timeout=10)
        except OSError:
            s.close()
            return

        def pump(src, dst):
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        t1 = threading.Thread(target=pump, args=(s, remote), daemon=True)
        t2 = threading.Thread(target=pump, args=(remote, s), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        s.close()
        remote.close()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = int(sys.argv[1])
    Server(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
