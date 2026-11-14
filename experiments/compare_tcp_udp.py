"""
TCP vs UDP comparison (separate from the chat app).

Sends N numbered messages over TCP or UDP and records, for each protocol:
how many arrived, whether they arrived in order, duplicates, one-way latency
and signs of recovery (TCP retransmission shows up as latency spikes, not loss).

Run sender and receiver on the SAME machine (loopback) so both use the same
clock and one-way latency is accurate. Controlled loss/delay is added with
Linux tc netem on the loopback interface (see netem.sh).

Typical use (on the Ubuntu VM or any Linux machine you own):
    python3 experiments/compare_tcp_udp.py run --label baseline --count 200
    sudo bash experiments/netem.sh loss 10
    python3 experiments/compare_tcp_udp.py run --label loss10 --count 200
    sudo bash experiments/netem.sh off

Results are printed and saved to:
    results/comparison.csv           one summary row per protocol per run
    results/raw/<label>_<proto>.csv  every message: seq, arrival order, latency

Manual two-terminal mode (e.g. across machines; latency then needs synced clocks):
    python3 experiments/compare_tcp_udp.py receive --proto udp --port 6000
    python3 experiments/compare_tcp_udp.py send    --proto udp --host <ip> --port 6000
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import statistics
import subprocess
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
UDP_END_REPEATS = 10      # the end marker can itself be lost, so repeat it
UDP_IDLE_TIMEOUT = 3.0    # stop waiting for UDP datagrams after this much silence


# ------------------------------------------------------------------ senders ---

def send_tcp(host: str, port: int, count: int, interval: float) -> None:
    sock = socket.create_connection((host, port), timeout=30)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # don't batch small messages
    for seq in range(count):
        sock.sendall(json.dumps({"seq": seq, "sent": time.time()}).encode() + b"\n")
        if interval:
            time.sleep(interval)
    sock.shutdown(socket.SHUT_WR)   # FIN: "no more data" - receiver's recv() returns b""
    sock.recv(1)                    # wait for receiver to close (all data acknowledged & read)
    sock.close()


def send_udp(host: str, port: int, count: int, interval: float) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for seq in range(count):
        sock.sendto(json.dumps({"seq": seq, "sent": time.time()}).encode(), (host, port))
        if interval:
            time.sleep(interval)
    for _ in range(UDP_END_REPEATS):
        sock.sendto(b'{"end": true}', (host, port))
        time.sleep(0.02)
    sock.close()


# ---------------------------------------------------------------- receivers ---

def receive_tcp(port: int, ready: threading.Event | None = None) -> list[tuple[int, float]]:
    """Return [(seq, latency_seconds)] in arrival order."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen(1)
    listener.settimeout(60)
    if ready:
        ready.set()
    conn, _ = listener.accept()
    conn.settimeout(120)
    arrivals, buf = [], b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        *lines, buf = buf.split(b"\n")   # framing: complete lines only
        now = time.time()
        for line in lines:
            msg = json.loads(line)
            arrivals.append((msg["seq"], now - msg["sent"]))
    conn.close()
    listener.close()
    return arrivals


def receive_udp(port: int, ready: threading.Event | None = None) -> list[tuple[int, float]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(UDP_IDLE_TIMEOUT)
    if ready:
        ready.set()
    arrivals = []
    while True:
        try:
            data, _ = sock.recvfrom(2048)   # one recvfrom() = one whole datagram (no framing needed)
        except socket.timeout:
            break
        msg = json.loads(data)
        if msg.get("end"):
            break
        arrivals.append((msg["seq"], time.time() - msg["sent"]))
    sock.close()
    return arrivals


# ------------------------------------------------------------------ analysis ---

def analyse(arrivals: list[tuple[int, float]], count: int) -> dict:
    seen, highest = set(), -1
    out_of_order = duplicates = 0
    for seq, _ in arrivals:
        if seq in seen:
            duplicates += 1
            continue
        if seq < highest:
            out_of_order += 1
        highest = max(highest, seq)
        seen.add(seq)
    lat_ms = sorted(lat * 1000 for _, lat in arrivals)
    median = statistics.median(lat_ms) if lat_ms else 0.0
    # A message delayed far beyond the median was most likely held back by a
    # TCP retransmission (head-of-line blocking) - the "recovery" signature.
    delayed = sum(1 for x in lat_ms if x > max(3 * median, median + 50))
    pct = lambda p: lat_ms[min(len(lat_ms) - 1, int(p / 100 * len(lat_ms)))] if lat_ms else None
    return {
        "sent": count,
        "received": len(seen),
        "lost": count - len(seen),
        "loss_pct": round(100 * (count - len(seen)) / count, 2),
        "in_order": out_of_order == 0,
        "out_of_order": out_of_order,
        "duplicates": duplicates,
        "lat_median_ms": round(median, 3),
        "lat_p95_ms": round(pct(95), 3) if lat_ms else None,
        "lat_max_ms": round(lat_ms[-1], 3) if lat_ms else None,
        "delayed_msgs": delayed,
    }


def netem_state() -> str:
    try:
        out = subprocess.run(["tc", "qdisc", "show", "dev", "lo"], capture_output=True, text=True, timeout=3).stdout
        line = next((l for l in out.splitlines() if "netem" in l), "")
        return line.split("netem", 1)[1].strip() if line else "none"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def save(label: str, proto: str, params: dict, arrivals, summary: dict) -> None:
    os.makedirs(os.path.join(RESULTS, "raw"), exist_ok=True)
    raw_path = os.path.join(RESULTS, "raw", f"{label}_{proto}.csv")
    with open(raw_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["arrival_index", "seq", "latency_ms"])
        for i, (seq, lat) in enumerate(arrivals):
            w.writerow([i, seq, round(lat * 1000, 3)])

    summary_path = os.path.join(RESULTS, "comparison.csv")
    row = {"time": datetime.now().isoformat(timespec="seconds"), "label": label, "proto": proto.upper(),
           **params, **summary}
    new = not os.path.exists(summary_path)
    with open(summary_path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def print_table(rows: list[tuple[str, dict]]) -> None:
    keys = list(rows[0][1])
    print(f"\n{'metric':16}" + "".join(f"{p.upper():>12}" for p, _ in rows))
    for k in keys:
        print(f"{k:16}" + "".join(f"{str(r[k]):>12}" for _, r in rows))


# ---------------------------------------------------------------------- main ---

def run_local(proto: str, port: int, count: int, interval: float):
    receiver = receive_tcp if proto == "tcp" else receive_udp
    sender = send_tcp if proto == "tcp" else send_udp
    result, ready = {}, threading.Event()
    t = threading.Thread(target=lambda: result.update(arrivals=receiver(port, ready)))
    t.start()
    ready.wait(10)
    sender("127.0.0.1", port, count, interval)
    t.join()
    return result["arrivals"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("run", help="run TCP and UDP locally and save results")
    r.add_argument("--label", required=True, help="condition name, e.g. baseline or loss10")
    r.add_argument("--proto", choices=["tcp", "udp", "both"], default="both")
    r.add_argument("--count", type=int, default=200)
    r.add_argument("--interval", type=float, default=0.01, help="seconds between messages")
    r.add_argument("--port", type=int, default=6000)

    rc = sub.add_parser("receive")
    rc.add_argument("--proto", choices=["tcp", "udp"], required=True)
    rc.add_argument("--port", type=int, default=6000)
    rc.add_argument("--count", type=int, default=200, help="number the sender will send")

    s = sub.add_parser("send")
    s.add_argument("--proto", choices=["tcp", "udp"], required=True)
    s.add_argument("--host", required=True)
    s.add_argument("--port", type=int, default=6000)
    s.add_argument("--count", type=int, default=200)
    s.add_argument("--interval", type=float, default=0.01)

    a = p.parse_args()

    if a.mode == "send":
        (send_tcp if a.proto == "tcp" else send_udp)(a.host, a.port, a.count, a.interval)
        print(f"Sent {a.count} {a.proto.upper()} messages to {a.host}:{a.port}")
        return
    if a.mode == "receive":
        arrivals = (receive_tcp if a.proto == "tcp" else receive_udp)(a.port)
        print_table([(a.proto, analyse(arrivals, a.count))])
        return

    netem = netem_state()
    print(f"Condition '{a.label}': {a.count} messages, {a.interval}s apart, loopback netem: {netem}")
    rows = []
    for proto in (["tcp", "udp"] if a.proto == "both" else [a.proto]):
        arrivals = run_local(proto, a.port, a.count, a.interval)
        summary = analyse(arrivals, a.count)
        save(a.label, proto, {"count": a.count, "interval_s": a.interval, "netem": netem}, arrivals, summary)
        rows.append((proto, summary))
    print_table(rows)
    print(f"\nSaved to {os.path.relpath(RESULTS)}/comparison.csv and {os.path.relpath(RESULTS)}/raw/")


if __name__ == "__main__":
    main()
