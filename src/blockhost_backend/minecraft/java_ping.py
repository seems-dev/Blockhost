import socket
import json
import struct

def java_server_ping(
    host: str,
    port: int,
    timeout_seconds: float = 1.0,
    protocol_version: int | None = None,
) -> dict:
    """
    Very basic Server List Ping for Minecraft Java.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds) as s:
            # Send Handshake
            # Packet ID 0, Protocol -1, Host, Port, Next State 1
            # We will just do a legacy ping for simplicity, or a modern handshake.
            # Modern handshake is easier if we use struct.
            
            def write_varint(val: int) -> bytes:
                out = b""
                while True:
                    byte = val & 0x7F
                    val >>= 7
                    if val:
                        out += bytes([byte | 0x80])
                    else:
                        out += bytes([byte])
                        break
                return out

            def read_varint(s: socket.socket) -> int:
                val = 0
                for i in range(5):
                    b = s.recv(1)
                    if not b:
                        raise ValueError("Socket closed")
                    b = b[0]
                    val |= (b & 0x7F) << (7 * i)
                    if not (b & 0x80):
                        break
                return val

            def write_string(val: str) -> bytes:
                b = val.encode("utf-8")
                return write_varint(len(b)) + b
            
            # Handshake packet
            handshake = b""
            handshake += write_varint(0x00) # Packet ID
            handshake += write_varint(protocol_version if protocol_version is not None else 765) # Protocol version
            handshake += write_string(host)
            handshake += struct.pack(">H", port)
            handshake += write_varint(1) # Next state: status
            
            p1 = write_varint(len(handshake)) + handshake
            s.sendall(p1)
            
            # Request packet
            req = write_varint(0x00)
            p2 = write_varint(len(req)) + req
            s.sendall(p2)
            
            # Read response
            length = read_varint(s)
            packet_id = read_varint(s)
            if packet_id != 0x00:
                raise ValueError("Invalid packet id")
                
            json_len = read_varint(s)
            data = b""
            while len(data) < json_len:
                chunk = s.recv(json_len - len(data))
                if not chunk:
                    break
                data += chunk
                
            return json.loads(data.decode("utf-8"))
    except Exception:
        return {}
