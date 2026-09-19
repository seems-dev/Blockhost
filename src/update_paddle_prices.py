import os
import sys

# Add src to python path so we can import blockhost_backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import BillingPlan
from sqlalchemy import select

def update_prices():
    # Map RAM size to paddle price id
    paddle_prices = {
        512: "pri_01m2a30sd71pwp2rpqxjqzdbay",
        1024: "pri_01m2a2z6s8j3h1e6jmpg6z8ay4",
        2048: "pri_01m2a2w94p4ptb9pkktfsxaymm",
        4096: "pri_01m28wxe05yva8az0vb6jymxmz",
        8192: "pri_01m2a32b1e7tayar7w8vej83hg"
    }

    with SessionLocal() as db:
        plans = db.execute(select(BillingPlan)).scalars().all()
        for plan in plans:
            if plan.ram_mb in paddle_prices:
                plan.provider_price_id = paddle_prices[plan.ram_mb]
                print(f"Updated plan '{plan.id}' ({plan.ram_mb} MB) -> {plan.provider_price_id}")
            else:
                print(f"Skipped plan '{plan.id}' ({plan.ram_mb} MB) - no mapping found")
        
        db.commit()
        print("Database updated successfully!")

if __name__ == "__main__":
    update_prices()
