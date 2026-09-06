import sys
import os
sys.path.insert(0, '/home/seems/blockhost/src')

from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Server, Node
from sqlalchemy import select

with SessionLocal() as db:
    nodes = db.execute(select(Node)).scalars().all()
    print("--- NODES ---")
    for n in nodes:
        print(f"Node {n.id}: {n.name} (IP: {n.ip_address}, Status: {n.status})")
        
    servers = db.execute(select(Server)).scalars().all()
    print("\n--- SERVERS ---")
    for s in servers:
        print(f"Server {s.id}: proxy_port={s.proxy_port}, node_id={s.node_id}, vm_ipv4={s.vm_ipv4}, vm_port={s.vm_port}, state={s.state}, flavor={s.flavor}")
