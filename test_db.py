from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Node

with SessionLocal() as db:
    nodes = db.query(Node).all()
    for n in nodes:
        print(f"Node: {n.name}, Status: {n.status}, Provider: {n.provider}, IP: {n.ip_address}")
