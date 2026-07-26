# -*- coding: utf-8 -*-
"""
Created on Wed Jul 15 19:26:03 2026

@author: daram
"""

import json
import random
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

PRODUCTS = [
    "Laptop", "Phone", "Tablet", "Monitor", "Keyboard",
    "Mouse", "Headphones", "Webcam", "SSD", "USB Hub"
]

def generate_orders(n=10000):
    orders = []
    for i in range(1, n + 1):
        order = {
            "order_id": i,
            "customer_name": fake.name(),
            "customer_email": fake.email(),
            "product": random.choice(PRODUCTS),
            "quantity": random.randint(1, 10),
            "price": round(random.uniform(10.0, 1500.0), 2),
            "city": fake.city(),
            "country": fake.country(),
            "order_date": fake.date_between(
                start_date="-2y",
                end_date="today"
            ).strftime("%Y-%m-%d")
        }
        orders.append(order)
    return orders

if __name__ == "__main__":
    print("Generating 10,000 orders...")
    orders = generate_orders(10000)
    with open("orders.json", "w") as f:
        json.dump(orders, f)
    print(f"Done. Sample record:")
    print(json.dumps(orders[0], indent=2))