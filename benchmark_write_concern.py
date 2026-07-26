import pymongo
import json
import time

# Load the generated orders
with open("orders.json", "r") as f:
    orders = json.load(f)

print("=" * 55)
print("MEASUREMENT 1: WRITE CONCERN COST — AWS")
print("=" * 55)
print("Question: How much does stronger durability")
print("cost in write speed on REAL distributed hardware?")
print()

# Connect to AWS primary using public IP
# directConnection=true connects to exactly this server
# Replication to secondaries happens internally via
# private IPs (10.0.1.126 and 10.0.1.195) — real network
client = pymongo.MongoClient(
    "mongodb://98.81.54.92:27017/?directConnection=true",
    serverSelectionTimeoutMS=10000
)
db = client["mongoguard"]

# Confirm connected to primary
hello = db.command("hello")
print(f"Connected to : {hello.get('me', 'unknown')}")
print(f"Is primary   : {hello.get('isWritablePrimary', False)}")
print(f"Replica set  : {hello.get('setName', 'unknown')}")
print()

NUM_RUNS = 5

# TEST 1: w:1 — only primary acknowledges
# Secondary servers at 44.205.7.220 and 3.235.19.128
# receive the data ASYNCHRONOUSLY after acknowledgment
# Risk: if primary crashes between acknowledgment and
# replication, data is permanently lost
print("TEST 1: w:1 (primary only acknowledges)")
print("Secondaries replicate asynchronously AFTER acknowledgment")
print()

w1_times = []
for run in range(NUM_RUNS):
    collection = db[f"orders_w1_run{run}"]
    collection.drop()
    start = time.time()
    collection.insert_many(orders)
    elapsed = round(time.time() - start, 3)
    w1_times.append(elapsed)
    print(f"  Run {run + 1}: {elapsed}s")

w1_avg = round(sum(w1_times) / len(w1_times), 3)
w1_best = round(min(w1_times), 3)
print(f"  Average : {w1_avg}s")
print(f"  Best    : {w1_best}s")
print()

# TEST 2: w:majority — majority must acknowledge
# Primary at 10.0.1.8 writes then WAITS for at least
# one secondary (10.0.1.126 OR 10.0.1.195) to confirm
# over the real AWS network before acknowledging
# This is where REAL network latency shows up —
# unlike Docker where containers share the same machine
print("TEST 2: w:majority (2 of 3 nodes must acknowledge)")
print("Primary WAITS for secondary confirmation across real network")
print()

wmaj_times = []
write_concern = pymongo.WriteConcern(w="majority")

for run in range(NUM_RUNS):
    collection = db[f"orders_wmaj_run{run}"]
    collection.drop()
    collection_safe = collection.with_options(
        write_concern=write_concern
    )
    start = time.time()
    collection_safe.insert_many(orders)
    elapsed = round(time.time() - start, 3)
    wmaj_times.append(elapsed)
    print(f"  Run {run + 1}: {elapsed}s")

wmaj_avg = round(sum(wmaj_times) / len(wmaj_times), 3)
wmaj_best = round(min(wmaj_times), 3)
print(f"  Average : {wmaj_avg}s")
print(f"  Best    : {wmaj_best}s")
print()

# Calculate overhead
diff = wmaj_avg - w1_avg
overhead_pct = abs(round((diff / w1_avg) * 100, 1))
overhead_label = "faster" if diff < 0 else "slower"
extra_ms = round(abs(diff) * 1000, 1)
per_doc_ms = round(abs(diff) / len(orders) * 1000, 4)

print("=" * 55)
print("RESULTS SUMMARY")
print("=" * 55)
print(f"w:1        average : {w1_avg}s")
print(f"w:majority average : {wmaj_avg}s")
print(f"Difference         : w:majority is {overhead_pct}% {overhead_label}")
print(f"Extra cost         : {extra_ms}ms total for 10,000 documents")
print(f"Per document       : {per_doc_ms}ms extra per document")
print()
print("WHAT THIS MEANS ON REAL AWS HARDWARE:")
if overhead_label == "slower" and overhead_pct > 5:
    print(f"w:majority adds {overhead_pct}% overhead because the primary")
    print(f"must wait for a secondary to confirm across the AWS")
    print(f"network before acknowledging. This is REAL network")
    print(f"latency — unlike Docker where everything shared RAM.")
    if overhead_pct < 20:
        print(f"At {overhead_pct}% the cost is small. Use w:majority.")
    elif overhead_pct < 50:
        print(f"At {overhead_pct}% consider w:majority for critical data")
        print(f"and w:1 for high-volume non-critical writes.")
    else:
        print(f"At {overhead_pct}% your secondaries may be lagging.")
        print(f"Investigate replication lag before enabling w:majority.")
else:
    print(f"w:majority shows minimal overhead on this infrastructure.")
    print(f"Use w:majority — durability is free at this scale.")

print()
print("COMPARISON TO LOCAL DOCKER RESULTS:")
print(f"Local Docker w:1        : 0.494s (no real network)")
print(f"Local Docker w:majority : 0.296s (virtual switch)")
print(f"AWS w:1        average  : {w1_avg}s (real network)")
print(f"AWS w:majority average  : {wmaj_avg}s (real network)")
print(f"AWS shows the TRUE cost of distributed durability.")

client.close()