import socket
import time
from protocol import FrameReader, encode, MSG_CHAT

# Connect to cloud VM
s = socket.create_connection(("102.37.218.84", 5000), timeout=5)
reader = FrameReader(s)

# Join
s.sendall(encode({"type": "join", "username": "FramingTester"}))
reader.read()  # consume join confirmation

print("[*] Case 1: Packet Coalescing (Multiple messages in one sendall call)")
# Three messages glued into a single buffer push
coalesced = (
    b'{"type":"msg","body":"Frame A"}\n'
    b'{"type":"msg","body":"Frame B"}\n'
    b'{"type":"msg","body":"Frame C"}\n'
)
s.sendall(coalesced)
print("    Dispatched 3 messages in a single continuous byte chunk.")

print("[*] Case 2: Packet Fragmentation (One message split across multiple byte bursts)")
# One message sent 2 bytes at a time with 50ms pause
fragmented = b'{"type":"msg","body":"Slow Stream Fragmentation"}\n'
for i in range(0, len(fragmented), 2):
    s.sendall(fragmented[i:i+2])
    time.sleep(0.05)
print("    Dispatched 1 message chopped into 2-byte chunks across multiple socket flushes.")

s.close()
print("[*] Framing test completed. Check server logs to verify all 4 messages arrived cleanly.")