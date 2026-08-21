import struct
import socket
import select
import time

class RconError(Exception):
    pass

class RconClient:
    SERVERDATA_AUTH = 3
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0
    SERVERDATA_AUTH_RESPONSE = 2

    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock = None
        self._req_id = 1

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect((self.host, self.port))
        except Exception as e:
            raise RconError(f"Failed to connect to RCON: {e}")

        # Authenticate
        self._send(self.SERVERDATA_AUTH, self.password)
        resp_id, resp_type, _ = self._recv()
        
        if resp_id == -1:
            raise RconError("RCON authentication failed")

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _send(self, packet_type: int, payload: str):
        payload_bytes = payload.encode('utf-8')
        packet_len = 10 + len(payload_bytes)
        packet = struct.pack(f"<iii{len(payload_bytes)}sBB", 
                             packet_len, self._req_id, packet_type, payload_bytes, 0, 0)
        self.sock.sendall(packet)

    def _recv(self) -> tuple[int, int, str]:
        # Read packet length
        length_bytes = self._read_exact(4)
        if not length_bytes:
            raise RconError("Connection closed by server")
        packet_len = struct.unpack("<i", length_bytes)[0]
        
        if packet_len < 10 or packet_len > 4096:
            raise RconError(f"Invalid RCON packet length: {packet_len}")

        # Read rest of packet
        packet_data = self._read_exact(packet_len)
        req_id, packet_type = struct.unpack("<ii", packet_data[:8])
        payload = packet_data[8:-2].decode('utf-8', errors='replace')
        return req_id, packet_type, payload

    def _read_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def command(self, cmd: str) -> str:
        if not self.sock:
            self.connect()
        
        self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        
        # We need to read until we get the response for this request ID.
        # Minecraft sometimes sends multiple response packets for long output.
        # But for simple commands like '/list', one packet is usually enough.
        # A robust way is to send a dummy packet after, and read until dummy response.
        # For simplicity, we just read one packet with a short timeout.
        
        resp_id, resp_type, payload = self._recv()
        
        # Read any remaining packets in buffer
        self.sock.settimeout(0.1)
        try:
            while True:
                _, _, extra_payload = self._recv()
                payload += extra_payload
        except (socket.timeout, RconError):
            pass
        finally:
            self.sock.settimeout(self.timeout)

        self._req_id += 1
        return payload
