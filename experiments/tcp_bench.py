"""
TCP measurement helpers for the practical investigations.

rtt         Round-trip time to the CHAT SERVER using the protocol's ping/pong.
            Run it against localhost and against the cloud server = two conditions.
              python3 experiments/tcp_bench.py rtt --host <ip> --port 5000 --count 50

sink        A plain TCP server that reads and discards data (throughput target).
            --slow makes it read slowly so the sender fills the receive window
            and Wireshark shows the window shrinking to zero (flow control).
              python3 experiments/tcp_bench.py sink --port 7000 [--slow]

throughput  Sends N megabytes to a sink and reports MB/s.
              python3 experiments/tcp_bench.py throughput --host 127.0.0.1 --port 7000 --mb 20

split       Framing demo: sends a join message in two halves with a pause.
            The server only replies once the newline arrives. Prints raw replies.
            Also sends one malformed line to show the error handling.
            (Windows-friendly replacement for the printf | nc demo.)
              python experiments/tcp_bench.py split --host <ip> --port 5000

abort       Joins the chat, sends one message, then closes with SO_LINGER=0.
            The OS sends RST instead of FIN: an "abrupt termination" you can
            capture and compare with a normal /quit (Experiment 3).
              python3 experiments/tcp_bench.py abort --host <ip> --port 5000
"""

from __future__ import annotations

import argparse
import os
import socket
import statistics
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from protocol import FrameReader, encode   # noqa: E402


def join_chat(host: str, port: int, username: str) -> tuple[socket.socket, FrameReader]:
    sock = socket.create_connection((host, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    reader = FrameReader(sock)
    sock.sendall(encode({"type": "join", "username": username}))
    reply = reader.read()
    if not reply or reply["type"] != "join_ok":
        sys.exit(f"Join failed: {reply}")
    local = sock.getsockname()
    print(f"Connected {local[0]}:{local[1]} -> {host}:{port} as {username}")
    return sock, reader


def rtt(host: str, port: int, count: int, username: str) -> None:
    sock, reader = join_chat(host, port, username)
    samples = []
    for _ in range(count):
        t0 = time.perf_counter()
        sock.sendall(encode({"type": "ping"}))
        while True:
            msg = reader.read()
            if msg is None:
                sys.exit("Server closed the connection")
            if msg["type"] == "pong":
                break
        samples.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.05)
    sock.sendall(encode({"type": "leave"}))
    sock.close()
    s = sorted(samples)
    print(f"\nApplication RTT over {count} ping/pong exchanges to {host}:{port}")
    print(f"  min {s[0]:.2f} ms | median {statistics.median(s):.2f} ms | "
          f"p95 {s[int(0.95 * (len(s) - 1))]:.2f} ms | max {s[-1]:.2f} ms")


def sink(port: int, slow: bool, rcvbuf: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if rcvbuf:
        # Must be set before listen() so it applies to accepted sockets and
        # is reflected in the window advertised during the handshake.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    listener.bind(("0.0.0.0", port))
    listener.listen(1)
    print(f"Sink listening on TCP {port} ({'SLOW reader' if slow else 'fast reader'}"
          f"{f', SO_RCVBUF={rcvbuf}' if rcvbuf else ''}). Ctrl+C to stop.")
    try:
        while True:
            conn, addr = listener.accept()
            total, t0 = 0, time.perf_counter()
            while True:
                data = conn.recv(1024 if slow else 262144)
                if not data:
                    break
                total += len(data)
                if slow:
                    time.sleep(0.05)   # ~20 KB/s: the receive buffer fills, window -> 0
            secs = time.perf_counter() - t0
            print(f"  {addr[0]}:{addr[1]} sent {total / 1e6:.2f} MB in {secs:.2f}s "
                  f"= {total / 1e6 / secs:.2f} MB/s")
            conn.close()
    except KeyboardInterrupt:
        pass


def throughput(host: str, port: int, mb: float) -> None:
    sock = socket.create_connection((host, port), timeout=60)
    block = b"x" * 65536
    total_bytes = int(mb * 1e6)
    sent, t0 = 0, time.perf_counter()
    while sent < total_bytes:
        sock.sendall(block[: min(len(block), total_bytes - sent)])
        sent += min(len(block), total_bytes - sent)
    sock.shutdown(socket.SHUT_WR)
    sock.recv(1)                       # wait until the sink has read everything and closed
    secs = time.perf_counter() - t0
    sock.close()
    print(f"Sent {sent / 1e6:.2f} MB in {secs:.2f}s = {sent / 1e6 / secs:.2f} MB/s "
          f"({sent * 8 / 1e6 / secs:.1f} Mbit/s)")


def split(host: str, port: int, username: str, pause: float) -> None:
    sock = socket.create_connection((host, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send each half immediately
    reader = FrameReader(sock)
    frame = encode({"type": "join", "username": username})
    half = len(frame) // 2
    print(f"Sending first half : {frame[:half]!r}")
    sock.sendall(frame[:half])
    print(f"Waiting {pause}s... (server has an incomplete message in its buffer, so no reply yet)")
    time.sleep(pause)
    print(f"Sending second half: {frame[half:]!r}")
    sock.sendall(frame[half:])
    print(f"Reply: {reader.read()}")
    print("Sending a malformed line: b'hello\\n'")
    sock.sendall(b"hello\n")
    print(f"Reply: {reader.read()}")
    sock.sendall(encode({"type": "leave"}))
    sock.close()


def abort(host: str, port: int, username: str) -> None:
    sock, _ = join_chat(host, port, username)
    sock.sendall(encode({"type": "message", "text": "about to crash"}))
    time.sleep(0.5)
    # SO_LINGER with timeout 0: close() discards unsent data and sends RST.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()
    print("Closed with RST (no FIN, no 'leave' message).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("rtt")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=5000)
    r.add_argument("--count", type=int, default=50)
    r.add_argument("--username", default="rtt_probe")

    s = sub.add_parser("sink")
    s.add_argument("--port", type=int, default=7000)
    s.add_argument("--slow", action="store_true")
    s.add_argument("--rcvbuf", type=int, default=0, help="receive buffer bytes (e.g. 16384)")

    t = sub.add_parser("throughput")
    t.add_argument("--host", default="127.0.0.1")
    t.add_argument("--port", type=int, default=7000)
    t.add_argument("--mb", type=float, default=20)

    sp = sub.add_parser("split")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=5000)
    sp.add_argument("--username", default="split_demo")
    sp.add_argument("--pause", type=float, default=2.0)

    a = sub.add_parser("abort")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=5000)
    a.add_argument("--username", default="crasher")

    args = p.parse_args()
    if args.mode == "rtt":
        rtt(args.host, args.port, args.count, args.username)
    elif args.mode == "sink":
        sink(args.port, args.slow, args.rcvbuf)
    elif args.mode == "throughput":
        throughput(args.host, args.port, args.mb)
    elif args.mode == "split":
        split(args.host, args.port, args.username, args.pause)
    else:
        abort(args.host, args.port, args.username)


if __name__ == "__main__":
    main()
