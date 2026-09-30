"""Raw-socket telnet client for GNS3 node consoles.

Console automation must survive two protocol quirks: telnet IAC negotiation
bytes interleaved with shell output (filtered in ``_strip_iac``), and a
fresh session replaying its boot banner (drained by ``exec``'s handshake).
"""
from __future__ import annotations

import base64
import re
import socket
import time

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# sized so that "printf '%s' '<chunk>'" stays under the ~255-byte canonical
# PTY line limit on debinet-style consoles
CHUNK_SIZE = 120


def _strip_iac_filter(data: bytes, pending: bytes) -> tuple[bytes, bytes]:
    """Removes telnet IAC sequences; caller keeps the unterminated tail."""
    b = pending + data
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c != 0xFF:
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            pending = b[i:]
            break
        cmd = b[i + 1]
        if cmd == 0xFF:
            out.append(0xFF)
            i += 2
        elif cmd in (0xFB, 0xFC, 0xFD, 0xFE):
            if i + 2 >= n:
                pending = b[i:]
                break
            i += 3
        elif cmd == 0xFA:
            j = b.find(b"\xF0", i + 2)
            if j == -1:
                pending = b[i:]
                break
            i = j + 1
        else:
            i += 2
    return bytes(out), pending


class Console:
    """Single-session telnet client for a node console."""

    def __init__(self, host: str, port: int, timeout: int = 10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buf = b""
        self._pending = b""

    def connect(self) -> "Console":
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(0.2)
        return self

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "Console":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def _recv(self) -> bytes:
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return b""
        if not data:
            raise ConnectionError("console closed")
        clean, self._pending = _strip_iac_filter(data, self._pending)
        return clean

    def send_line(self, line: str) -> None:
        self.sock.sendall(line.encode() + b"\n")

    def read_until(self, marker: bytes, timeout: float) -> tuple[bool, str]:
        """Waits for ``marker`` in the stream; returns (found, text so far)."""
        deadline = time.time() + timeout
        buf = self.buf
        while time.time() < deadline:
            idx = buf.find(marker)
            if idx != -1:
                consumed, rest = buf[: idx + len(marker)], buf[idx + len(marker):]
                self.buf = rest
                return True, consumed.decode(errors="replace")
            try:
                buf += self._recv()
            except ConnectionError:
                self.buf = buf
                return False, buf.decode(errors="replace")
        self.buf = buf
        return False, buf.decode(errors="replace")

    def drain(self, timeout: float = 25.0) -> None:
        """Drops any stale session banner so later reads stay clean."""
        self.buf = b""
        self.send_line("echo RS$?RS")
        self.read_until(b"RS0RS", timeout)

    def exec(self, command: str, timeout: float = 30.0) -> str:
        """Runs one shell command on the node, returns its cleaned output."""
        self.drain()
        tag = f"X{time.time_ns() % 937:03d}"
        self.buf = b""
        self.send_line(f"{command}; echo {tag}$?{tag}")
        ok, out = self.read_until((tag + "0" + tag).encode(), timeout)
        cleaned = _ANSI_RE.sub("", out)
        return cleaned.split("\n", 1)[-1]


def push_file(
    host: str,
    port: int,
    path: str,
    content: str,
    mode: int | None = None,
    timeout: float = 120.0,
) -> str:
    """Writes ``content`` to ``path`` through a console session (base64
    chunked transport, works for any path, /root included). Returns a
    short summary of the written size."""
    payload = base64.b64encode(content.encode()).decode()
    chunks = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
    tmp = f"/tmp/.gns3api-{time.time_ns() % 9973:04d}.b64"

    with Console(host, port) as con:
        con.drain()
        con.send_line(f"rm -f {tmp}")
        for chunk in chunks:
            con.send_line(f"printf '%s' '{chunk}' >> {tmp}")
            time.sleep(0.08)
        expected = str(len(payload))
        con.send_line(f"echo SZ$(wc -c <{tmp})Z")
        ok, _ = con.read_until(f"SZ{expected}Z".encode(), timeout)
        if not ok:
            raise RuntimeError(f"upload size mismatch (want {expected} base64 bytes) - {path}")

        if mode is not None:
            con.send_line(
                f"base64 -d {tmp} > {path} && chmod {mode:o} {path} && rm -f {tmp}; echo DC$?DC"
            )
        else:
            con.send_line(f"base64 -d {tmp} > {path} && rm -f {tmp}; echo DC$?DC")
        ok, out = con.read_until(b"DC0DC", timeout)
        if not ok:
            raise RuntimeError(f"decode to {path} failed; stream tail: {out[-160:]!r}")

        con.send_line(f"echo WD$(wc -c <{path})W")
        ok, out = con.read_until(f"WD{len(content)}W".encode(), timeout)
        if not ok:
            raise RuntimeError(f"after-write size check failed for {path} - {out[-120:]}")

    return f"written {path}: {len(content)} bytes" + (f" (mode {mode:o})" if mode else "")


def pull_file(host: str, port: int, path: str, timeout: float = 60.0) -> str:
    """Reads ``path`` through a console session (any path, /root included).
    The node base64-encodes the file into one output line, so binary
    content survives the transport; the caller receives the decoded text."""
    with Console(host, port) as con:
        con.drain()
        tag = f"PL{time.time_ns() % 937:03d}"
        con.buf = b""
        con.send_line(f"base64 -w0 {path} 2>/dev/null; echo {tag}$?{tag}")
        ok, out = con.read_until((tag + "0" + tag).encode(), timeout)
        if not ok:
            raise RuntimeError(f"read of {path} timed out - partial stream tail: {out[-160:]!r}")

        body = _ANSI_RE.sub("", out).split("\n", 1)[-1]
        body = body[: body.rfind(tag)]
        # bracketed-paste toggles (esc[?2004l) ride along inside long output
        # echo - without stripping them the payload misaligns
        payload = "".join(body.split())
        if not payload:
            raise RuntimeError(f"{path} unreadable or empty on the node")
        return base64.b64decode(payload).decode()
