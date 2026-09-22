import asyncio
import json
import struct

async def java_server_ping(
    host: str,
    port: int,
    timeout_seconds: float = 1.0,
    protocol_version: int | None = None,
) -> dict:
    """
    Very basic Server List Ping for Minecraft Java asynchronously.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 
            timeout=timeout_seconds
        )
        
        try:
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

            async def read_varint(reader: asyncio.StreamReader) -> int:
                val = 0
                for i in range(5):
                    b = await asyncio.wait_for(reader.readexactly(1), timeout=timeout_seconds)
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
            writer.write(p1)
            
            # Request packet
            req = write_varint(0x00)
            p2 = write_varint(len(req)) + req
            writer.write(p2)
            await writer.drain()
            
            # Read response
            length = await read_varint(reader)
            packet_id = await read_varint(reader)
            if packet_id != 0x00:
                raise ValueError("Invalid packet id")
                
            json_len = await read_varint(reader)
            data = await asyncio.wait_for(reader.readexactly(json_len), timeout=timeout_seconds)
            return json.loads(data.decode("utf-8"))
        finally:
            writer.close()
            await writer.wait_closed()
            
    except Exception:
        return {}
