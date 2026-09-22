import uuid
import sys
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.services.node_capacity import select_best_node

with SessionLocal() as db:
    try:
        node = select_best_node(db, required_ram_mb=0)
        print(f"Success! Node: {node}")
    except Exception as e:
        print(f"Exception: {e}")
        sys.exit(1)
