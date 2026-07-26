import pymongo
import time

ALL_PUBLIC_IPS = [
    "3.235.101.209",
    "18.234.184.58",
    "3.235.104.214"
]

print("=" * 55)
print("MEASUREMENT 4: READ SCALING EFFICIENCY")
print("=" * 55)
print("Question: Does distributing reads across all three")
print("replica set members actually improve throughput?")
print()
print("Theory says 3 nodes = 3x throughput.")
print("Reality is always less due to replication overhead.")
print("This measurement finds the ACTUAL scaling factor.")
print()

# Step 1: Find current primary
print("Step 1: Detecting current replica set members...")
PRIMARY_PUBLIC = None
SECONDARY_IPS = []

for ip in ALL_PUBLIC_IPS:
    try:
        test = pymongo.MongoClient(
            f"mongodb://{ip}:27017/?directConnection=true",
            serverSelectionTimeoutMS=5000
        )
        hello = test["mongoguard"].command("hello")
        if hello.get("isWritablePrimary"):
            PRIMARY_PUBLIC = ip
            print(f"  Primary   : {ip}")
        else:
            SECONDARY_IPS.append(ip)
            print(f"  Secondary : {ip}")
        test.close()
    except Exception as e:
        print(f"  {ip}: not reachable")

if not PRIMARY_PUBLIC:
    print("No primary found. Check replica set.")
    exit(1)

print()

# Step 2: Insert test data
print("Step 2: Inserting 50,000 test documents...")
primary_client = pymongo.MongoClient(
    f"mongodb://{PRIMARY_PUBLIC}:27017/?directConnection=true"
)
collection = primary_client["mongoguard"]["read_test"]
collection.drop()

docs = [
    {
        "_id": i,
        "product": ["Laptop", "Phone", "Tablet", "Monitor", "Keyboard"][i % 5],
        "price": round(100 + (i % 1000) * 1.5, 2),
        "quantity": i % 20 + 1,
        "country": ["US", "UK", "NG", "DE", "FR"][i % 5]
    }
    for i in range(50000)
]

start = time.time()
# Insert in batches of 1000
for i in range(0, len(docs), 1000):
    collection.insert_many(docs[i:i+1000])
insert_time = round(time.time() - start, 2)
print(f"  Inserted 50,000 documents in {insert_time}s")

# Wait for secondaries to catch up
print("  Waiting 5 seconds for replication...")
time.sleep(5)
print()

NUM_READS = 1000

# Step 3: All reads from PRIMARY only
print("Step 3: Running reads — PRIMARY only...")
print(f"  Sending all {NUM_READS} reads to primary ({PRIMARY_PUBLIC})")

primary_read_client = pymongo.MongoClient(
    f"mongodb://{PRIMARY_PUBLIC}:27017/?directConnection=true"
)
primary_col = primary_read_client["mongoguard"]["read_test"]

start = time.time()
for i in range(NUM_READS):
    list(primary_col.find(
        {"product": "Laptop"},
        {"_id": 0, "price": 1}
    ).limit(10))
primary_time = round(time.time() - start, 3)
primary_per_read = round(primary_time / NUM_READS * 1000, 3)

print(f"  Total time : {primary_time}s")
print(f"  Per read   : {primary_per_read}ms")
print()

# Step 4: Reads distributed across ALL THREE members
print("Step 4: Running reads — distributed across all three members...")
print("  Using readPreference=nearest — pymongo picks fastest available")

replica_set_uri = (
    f"mongodb://{ALL_PUBLIC_IPS[0]}:27017,"
    f"{ALL_PUBLIC_IPS[1]}:27017,"
    f"{ALL_PUBLIC_IPS[2]}:27017/"
    f"?replicaSet=rs0&readPreference=nearest"
)
distributed_client = pymongo.MongoClient(replica_set_uri)
distributed_col = distributed_client["mongoguard"]["read_test"]

start = time.time()
for i in range(NUM_READS):
    list(distributed_col.find(
        {"product": "Laptop"},
        {"_id": 0, "price": 1}
    ).limit(10))
distributed_time = round(time.time() - start, 3)
distributed_per_read = round(distributed_time / NUM_READS * 1000, 3)

print(f"  Total time : {distributed_time}s")
print(f"  Per read   : {distributed_per_read}ms")
print()

# Step 5: Calculate scaling efficiency
improvement = round(primary_time / distributed_time, 2)
efficiency_pct = round((improvement / 3) * 100, 1)
time_saved = round(primary_time - distributed_time, 3)

print("=" * 55)
print("MEASUREMENT 4 RESULTS")
print("=" * 55)
print(f"Primary only   : {primary_time}s ({primary_per_read}ms per read)")
print(f"Distributed    : {distributed_time}s ({distributed_per_read}ms per read)")
print(f"Improvement    : {improvement}x faster with distributed reads")
print(f"Efficiency     : {efficiency_pct}% of theoretical 3x maximum")
print(f"Time saved     : {time_saved}s per {NUM_READS} reads")
print()
print("WHAT THIS MEANS:")
if improvement >= 2.5:
    print(f"Excellent scaling — {improvement}x improvement.")
    print("Distributing reads across secondaries is highly effective.")
    print("Recommend: use readPreference=nearest for read-heavy workloads.")
elif improvement >= 1.5:
    print(f"Good scaling — {improvement}x improvement.")
    print("Distributing reads helps but secondaries carry replication overhead.")
    print("Recommend: use secondary reads for non-critical reads only.")
else:
    print(f"Limited scaling — only {improvement}x improvement.")
    print("Secondaries are spending significant CPU on replication.")
    print("Adding dedicated read replicas would improve this.")
print()
print("WHY SCALING IS LESS THAN 3x:")
print("Secondaries spend CPU on two tasks simultaneously:")
print("  1. Serving read queries from your application")
print("  2. Applying oplog entries from the primary (replication)")
print("That replication overhead reduces their available read capacity.")
print("The actual improvement depends on your write rate.")
print("Higher write rate = more replication work = less read capacity.")

primary_client.close()
primary_read_client.close()
distributed_client.close()