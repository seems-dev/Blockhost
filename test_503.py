import uuid
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.node_capacity import select_best_node
from blockhost_backend.database.schema import Node
db = SessionLocal()
settings = get_settings()
print(f"Production mode: {settings.production_mode}")
node = select_best_node(db)
print(f"Selected node: {node}")
db.close()
