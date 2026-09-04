import sys
from sqlalchemy import create_engine, text

# Use the production DATABASE_URL if available, otherwise fallback
import os
from dotenv import load_dotenv
load_dotenv("deploy/.env")
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL found")
    sys.exit(1)

engine = create_engine(db_url)
with engine.connect() as conn:
    result = conn.execute(text("SELECT id, world_name, proxy_port, vm_ipv4, vm_port, mc_version FROM servers WHERE state='running' OR state='created'"))
    for row in result:
        print(f"ID: {row[0][:8]}, Name: {row[1]}, Proxy: {row[2]}, Backend: {row[3]}:{row[4]}, Ver: {row[5]}")
