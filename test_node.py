import sys
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.services.node_capacity import select_best_node
from blockhost_backend.database.schema import Node

db = SessionLocal()
nodes = db.query(Node).all()
for n in nodes:
    print(f"Node {n.name}: status={n.status}, used_ram={n.used_ram_mb}, total_ram={n.total_ram_mb}, provider={n.provider}")

node = select_best_node(db)
print(f"select_best_node returned: {node.name if node else None}")
