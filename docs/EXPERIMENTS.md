# TCP investigations and TCP/UDP comparison

This guide covers the four compulsory TCP experiments and the TCP vs UDP comparison. Each section gives the commands to reproduce it, the Wireshark filters to use, and what to annotate in your screenshots.

## Ground rules

- Run disruptive tests (netem loss and delay) **only on loopback (`lo`) on your own VM or Linux machine**. `netem.sh` touches only `lo` unless you deliberately override it.
- **Always run `sudo bash experiments/netem.sh off` when you finish.**
- Use made-up chat text in all captures (e.g. "test message 1"). Never type real passwords or personal information while capturing.
- Save each capture in `captures/` using the file names below. They are part of the submission.

## Where each command runs

- Commands with `sudo`, `tcpdump`, `netem.sh` or `python3` run **on the EC2 VM**. From Windows, open PowerShell and connect with `ssh -i .\key.pem ubuntu@<PUBLIC_IP>`; you can open several PowerShell windows for several SSH sessions ("terminal 1, 2, 3").
- Commands marked **laptop** run in PowerShell on Windows, using `python` instead of `python3` and `\` in paths.
- Wireshark runs on the laptop. It opens the `.pcap` files you download from the VM.

## Setup (once, on the Ubuntu VM)

```bash
sudo apt-get install -y tcpdump iproute2 linux-modules-extra-$(uname -r)
cd ~/tcp-chat           # the project folder you copied/cloned
mkdir -p captures results
```

To download captures to your laptop for Wireshark:
```bash
scp -i key.pem ubuntu@<PUBLIC_IP>:~/tcp-chat/captures/*.pcap ./captures/
```

Useful Wireshark settings:
- Edit → Preferences → Protocols → TCP → untick "Relative sequence numbers" if you want to show the real initial sequence numbers (ISN). Show both views if you can: relative numbers are easier to read, raw numbers prove the ISN is random.
- Statistics → Conversations → TCP shows each 4-tuple.
- Statistics → TCP Stream Graphs → Round Trip Time / Throughput / Window Scaling.

---

## Experiment 1: Connection establishment (three-way handshake)

**Goal:** capture SYN, SYN-ACK and ACK, and identify the IP addresses, ports, sequence numbers and acknowledgement numbers.

Use the real cloud connection, so the capture shows public IPs.

1. On your **laptop**, start Wireshark on your Wi-Fi/Ethernet interface with the capture filter `tcp port 5000`.
2. Connect: `python client.py --host <PUBLIC_IP> --port 5000 --username alice`
3. Send one message, then type `/quit`.
4. Stop and save the capture as `captures/exp1_handshake.pcapng`.

Optionally capture the same connection on the server side as well:
`sudo tcpdump -i any -w captures/exp1_server.pcap 'tcp port 5000'`

**Display filter:** `tcp.flags.syn == 1 || tcp.stream == 0`

**Annotate:**
| Packet | Flags | Src IP:port | Dst IP:port | Seq | Ack |
|---|---|---|---|---|---|
| 1 | SYN | laptop IP : ephemeral port (e.g. 54356) | server IP : 5000 | ISN_c | 0 |
| 2 | SYN, ACK | server : 5000 | laptop : 54356 | ISN_s | ISN_c + 1 |
| 3 | ACK | laptop | server | ISN_c + 1 | ISN_s + 1 |

Also point out:
- **MSS** and **window scale** options in the SYN
- The first data segment (PSH, ACK) carrying `{"type":"join",...}`. Right-click → Follow → TCP Stream shows the newline-delimited JSON.
- That the server port is fixed (5000) while the client port is chosen by the OS

If your laptop is behind NAT (most home and mobile networks), the source IP the server sees is your router's public IP, not your laptop's private IP. Comparing the two captures shows this.

---

## Experiment 2: Packet loss and retransmission

**Goal:** add controlled loss and show TCP retransmitting and recovering.

Run on the VM over loopback:

```bash
# terminal 1: capture
sudo tcpdump -i lo -w captures/exp2_loss.pcap 'tcp port 5000'

# terminal 2: server on loopback
python3 server.py --port 5000          # or use the running service

# terminal 3: add 10% loss, generate traffic, remove loss
sudo bash experiments/netem.sh loss 10
python3 experiments/tcp_bench.py rtt --host 127.0.0.1 --port 5000 --count 100
sudo bash experiments/netem.sh off
```

If the systemd service is already using port 5000, either run the experiment against it or start a second server with `--port 5001` and capture that port instead.

**Display filters:**
- `tcp.analysis.retransmission || tcp.analysis.fast_retransmission`
- `tcp.analysis.duplicate_ack`
- `tcp.analysis.lost_segment`

**Annotate:**
- A segment that was dropped, followed by its retransmission (same sequence number)
- Whether it was a timeout retransmission (RTO) or a fast retransmission after duplicate ACKs
- The RTT samples: in the `rtt` output, max and p95 jump compared with the baseline. Those are the pings delayed by retransmissions.
- The key point: **the application received every ping's pong, in order, with no errors. TCP hid the loss and turned it into delay.**

Also run the chat client under loss (two clients on the VM, `/msg` back and forth) to show that the messages still arrive intact.

---

## Experiment 3: Connection termination (normal vs abrupt)

**Goal:** compare FIN-based and RST-based closes, and show the server's cleanup.

```bash
# on the VM
sudo tcpdump -i any -w captures/exp3_termination.pcap 'tcp port 5000'
sudo journalctl -u chat-server -f          # second terminal: server log
```

From your laptop, run these one at a time:

| Case | Command | Expected on the wire | Expected server log |
|---|---|---|---|
| A. Normal | client, then `/quit` | `leave` message, FIN/ACK in both directions | `alice left` |
| B. Process killed | client, then close its PowerShell window (or Task Manager → End task) | The OS usually still sends FIN, since the kernel closes the socket. Windows may send RST instead, which is worth noting if you see it | `connection closed` (or `connection reset`) |
| C. Abort (RST) | `python experiments/tcp_bench.py abort --host <PUBLIC_IP>` | RST, no FIN | `connection reset` |
| D. Network lost (optional) | client, then turn Wi-Fi off for 70 s | Nothing; later the server's keepalive/idle timeout fires | `timed out` |

**Display filter:** `tcp.flags.fin == 1 || tcp.flags.reset == 1`

**Annotate:**
- The four-way close in case A (FIN → ACK, FIN → ACK, sometimes combined into FIN,ACK)
- The RST in case C, and that no FIN exchange happens
- Which side enters TIME_WAIT (the side that closes first). On the VM, `ss -tan state time-wait` shows it.
- The matching server log lines, and the "bob left" / "bob disconnected (connection reset)" notices the other clients received

**Explain the server-side cleanup:** `recv()` returns `b""` on FIN or raises `ConnectionResetError` on RST. The `finally` block in `server.py` then removes the user from the shared dictionary under a lock, stops the writer thread, closes the socket and tells everyone else. Case D shows what TCP *cannot* do: with no FIN or RST, the server can't tell a silent peer from a dead one, so it relies on timeouts.

---

## Experiment 4: Flow control and performance

**Goal:** measure latency/RTT and throughput under two conditions, and look at the advertised receive window.

### 4a. RTT: two conditions

```bash
# Condition 1: same machine (on the VM)
python3 experiments/tcp_bench.py rtt --host 127.0.0.1 --port 5000 --count 50
# Condition 2: over the internet (from your laptop)
python experiments/tcp_bench.py rtt --host <PUBLIC_IP> --port 5000 --count 50
```
Optional third condition on the VM: `sudo bash experiments/netem.sh delay 50`, run again, then `netem.sh off`.

### 4b. Throughput: two conditions, with window analysis

```bash
# terminal 1 (VM)
sudo tcpdump -i lo -s 128 -w captures/exp4_flow.pcap 'tcp port 7000'
# terminal 2 (VM): receiver
python3 experiments/tcp_bench.py sink --port 7000

# terminal 3 (VM)
python3 experiments/tcp_bench.py throughput --port 7000 --mb 50              # condition 1: no impairment
sudo bash experiments/netem.sh delay 25
python3 experiments/tcp_bench.py throughput --port 7000 --mb 20              # condition 2: +50 ms RTT
sudo bash experiments/netem.sh off
```

### 4c. Flow control: slow receiver

```bash
python3 experiments/tcp_bench.py sink --port 7000 --slow --rcvbuf 16384     # terminal 2
python3 experiments/tcp_bench.py throughput --port 7000 --mb 1              # terminal 3
```

**Display filters:** `tcp.analysis.zero_window || tcp.analysis.window_full || tcp.analysis.zero_window_probe`
Also open Statistics → TCP Stream Graphs → Window Scaling for the sink connection.

**Annotate and explain:**
- The `Win=` field shrinking as the slow receiver's buffer fills, reaching **zero window**, then the sender's zero-window probes, then a window update when the receiver reads. This is flow control: the receiver limits the sender.
- Throughput drops sharply once RTT rises. With a fixed window, throughput ≤ window ÷ RTT. Do the calculation with your own numbers.
- Note that a capture taken with a snap length (`-s 128`) records headers only, which keeps files small.

Record the results:

| Condition | RTT median | RTT p95 | Throughput (MB/s) | Observed window behaviour |
|---|---|---|---|---|
| Loopback, no netem | | | | |
| Loopback, +50 ms RTT (netem) | | | | |
| Laptop → cloud | | | – | |
| Slow receiver | – | – | | Zero window at packet # |

---

## TCP vs UDP comparison

The brief asks for at least 100 numbered messages per protocol, under baseline and controlled-loss conditions.

```bash
# on the VM
sudo tcpdump -i lo -w captures/compare_loss10.pcap 'port 6000'      # optional capture
python3 experiments/compare_tcp_udp.py run --label baseline --count 200
sudo bash experiments/netem.sh loss 10
python3 experiments/compare_tcp_udp.py run --label loss10 --count 200
sudo bash experiments/netem.sh both 20 10
python3 experiments/compare_tcp_udp.py run --label delay20_loss10 --count 200
sudo bash experiments/netem.sh off
```

Each run sends the same number of messages at the same rate over **both** TCP and UDP on the same path, so the conditions are comparable. Outputs:
- `results/comparison.csv`: one row per protocol per condition (sent, received, lost, in order, duplicates, latency median/p95/max, delayed messages, netem setting)
- `results/raw/<label>_<proto>.csv`: every message's sequence number, arrival position and latency

**Expected pattern (check against your own numbers):**

| | TCP | UDP |
|---|---|---|
| Baseline | All received, in order | All (or nearly all) received, usually in order |
| 10% loss | **All received, in order**; p95/max latency jumps (retransmission delay, head-of-line blocking) | ~10% missing; the receiver is never told; no retries |
| Recovery | Automatic (retransmission) | None. The application would need its own ACKs, retries and ordering |

netem's loss model is random per packet, so UDP reordering may be rare. To show reordering explicitly, add `reorder` (e.g. `tc qdisc replace dev lo root netem delay 10ms reorder 25% 50%`) as an extra condition.

### What the results do and do not prove

These are the points to discuss in the report:
- They **do** show that, on this path and under these conditions, TCP delivered every message in order while UDP lost messages silently.
- They **do not** prove anything about real internet loss patterns. netem drops packets at random. Real networks drop in bursts, and loss correlates with congestion.
- Loopback has almost no RTT, so retransmission timers behave differently than over a long path.
- 200 messages is a small sample. Repeat each condition (e.g. 3 runs) and report the range.
- netem on `lo` drops packets in both directions, including TCP's ACKs.
- One-way latency is only valid because sender and receiver share one clock (same machine).

---

## Extra report points the brief asks for

- **Why TCP needs application-level framing:** see `protocol.py` and the tests `test_message_split_across_several_tcp_segments` and `test_several_messages_in_one_send`.
- **What TCP cannot guarantee after a connection failure:**
  - When `sendall()` returns, the data is only in the local kernel buffer. It doesn't mean the peer's application read it.
  - If the connection dies, data that was sent but not yet acknowledged may or may not have been delivered, and the sender can't tell which.
  - Recovering from that (reconnecting and resending, or detecting duplicates) is the application's job.
- **How the captures support the conclusions:** map each claim in the analysis to a packet number in the capture files.
