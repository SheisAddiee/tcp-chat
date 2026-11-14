"""
Wire protocol shared by the server, the client and the tests.

TCP gives us a reliable, ordered BYTE STREAM. It does not preserve message
boundaries: one send() on one side can arrive as several recv() calls on the
other, and several sends can arrive in a single recv(). So the application
has to mark where each message ends. This is called framing.

Framing used here: newline-delimited JSON (one JSON object per line).
json.dumps() always escapes newline characters inside strings as "\\n",
so a raw b"\\n" byte can only ever mean "end of message".
"""

from __future__ import annotations

import json
import socket
from typing import Optional

ENCODING = "utf-8"
DELIMITER = b"\n"
MAX_FRAME_BYTES = 16 * 1024   # reject anything larger (protects server memory)
RECV_CHUNK = 4096

# --- Protocol Message Types ---
MSG_JOIN = "join"
MSG_CHAT = "msg"
MSG_DIRECT = "direct_message"
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_ERROR = "error"
MSG_SYSTEM = "system"


class ProtocolError(Exception):
    """A single frame was malformed. The stream itself is still usable."""


class FrameTooLarge(Exception):
    """The peer sent more than MAX_FRAME_BYTES without a delimiter."""


def encode(message: dict) -> bytes:
    """Turn a dict into one frame: compact JSON + newline."""
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(ENCODING)
    if len(data) > MAX_FRAME_BYTES:
        raise ProtocolError("message too large")
    return data + DELIMITER


# --- Message Builder Helpers ---
def make_join(username: str) -> dict:
    return {"type": MSG_JOIN, "username": username}

def make_chat(body: str) -> dict:
    return {"type": MSG_CHAT, "body": body}

def make_direct(to_user: str, body: str) -> dict:
    return {"type": MSG_DIRECT, "to": to_user, "body": body}

def make_ping() -> dict:
    return {"type": MSG_PING}

def make_pong() -> dict:
    return {"type": MSG_PONG}

def make_system(body: str) -> dict:
    return {"type": MSG_SYSTEM, "body": body}

def make_error(body: str) -> dict:
    return {"type": MSG_ERROR, "body": body}


class FrameReader:
    """
    Reads complete frames from a TCP socket.

    Bytes from recv() are added to a buffer. Whenever the buffer contains a
    newline, everything before it is one complete message. Anything after it
    stays in the buffer for the next call.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def read(self) -> Optional[dict]:
        """
        Return the next message, or None if the peer closed the connection.

        Raises ProtocolError for a bad frame (caller may continue reading),
        FrameTooLarge if the peer floods us, and the usual socket errors
        (ConnectionResetError, socket.timeout, ...) from recv().
        """
        while True:
            end = self.buffer.find(DELIMITER)
            if end != -1:
                line = bytes(self.buffer[:end])
                del self.buffer[: end + 1]
                if not line.strip():
                    continue  # ignore blank lines
                return _decode(line)

            if len(self.buffer) > MAX_FRAME_BYTES:
                raise FrameTooLarge()

            chunk = self.sock.recv(RECV_CHUNK)
            if not chunk:
                # recv() returning b"" means the peer sent FIN:
                # an orderly TCP close. No more data will arrive.
                return None
            self.buffer.extend(chunk)


def _decode(line: bytes) -> dict:
    try:
        message = json.loads(line.decode(ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("message must be a JSON object with a 'type' field")
    return message