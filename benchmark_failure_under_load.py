import pymongo
import paramiko
import time
import threading

ALL_PUBLIC_IPS = [
    "3.235.101.209",
    "18.234.184.58",
    "3.235.104.214"
]
KEY_FILE = "mongoguard-key.pem"
SSH_USER = "ec2-user"

print("=" * 55)
print("MEASUREMENT 7: FAILURE UNDER REALISTIC LOAD")
print("=" * 55)
print("Question: How does election time change when")
print("REAL mixed traffic is running versus synthetic")
print("inserts only?")
print()
print("Measurement 3 used inserts only.")
print("Real applications mix reads + writes + aggregations.")
print("This measures election time under realistic load.")
print()

# Step 1: Find primary
print("Step 1: Detecting current primary...")
PRIMARY_PUBLIC = None
for ip in ALL_PUBLIC_IPS:
    try:
        test = pymongo.MongoClient(
            f"mongodb://{ip}:27017/?directConnection=true",
            serverSelectionTimeoutMS=5000
        )
        hello = test["mongoguard"].command("hello")
        if hello.get("isWritablePrimary"):
            PRIMARY_PUBLIC = ip
            print(f"  Primary: {ip}")
        test.close()
    except Exception:
        pass

SECONDARIES = [ip for ip in ALL_PUBLIC_IPS if ip != PRIMARY_PUBLIC]
REPLICA_SET_URI = (
    f"mongodb://{ALL_PUBLIC_IPS[0]}:27017,"
    f"{ALL_PUBLIC_IPS[1]}:27017,"
    f"{ALL_PUBLIC_IPS[2]}:27017/"
    f"?replicaSet=rs0&serverSelectionTimeoutMS=60000"
)

# Step 2: Prepare test data
print("\nStep 2: Preparing test data...")
setup_client = pymongo.MongoClient(
    f"mongodb://{PRIMARY_PUBLIC}:27017/?directConnection=true"
)
col = setup_client["mongoguard"]["load_test"]
col.drop()
docs = [
    {
        "_id": i,
        "product": ["Laptop","Phone","Tablet","Monitor","Keyboard"][i % 5],
        "price": round(100 + (i % 1000) * 1.5, 2),
        "quantity": i % 20 + 1,
        "country": ["US","UK","NG","DE","FR"][i % 5]
    }
    for i in range(10000)
]
col.insert_many(docs)
print(f"  Inserted 10,000 documents")
setup_client.close()
time.sleep(3)

# Shared state
results = []
results_lock = threading.Lock()
stop_all = threading.Event()
kill_time = [None]

# Step 3: Define realistic mixed workload threads
def writer_thread(primary_ip):
    """Continuously inserts new documents — simulates new orders arriving"""
    client = pymongo.MongoClient(
        f"mongodb://{primary_ip}:27017/?directConnection=true",
        serverSelectionTimeoutMS=3000,
        socketTimeoutMS=3000
    )
    collection = client["mongoguard"]["load_test"]
    i = 10000
    while not stop_all.is_set():
        try:
            collection.insert_one({"_id": i, "product": "Laptop", "ts": time.time()})
            with results_lock:
                results.append(("write", "success", time.time()))
        except Exception:
            with results_lock:
                results.append(("write", "failure", time.time()))
        i += 1
        time.sleep(0.3)
    client.close()

def reader_thread():
    """Continuously reads documents — simulates users browsing"""
    client = pymongo.MongoClient(
        REPLICA_SET_URI,
        directConnection=False,
        serverSelectionTimeoutMS=3000,
        socketTimeoutMS=3000
    )
    collection = client["mongoguard"]["load_test"]
    i = 0
    while not stop_all.is_set():
        try:
            list(collection.find(
                {"product": "Laptop"},
                {"price": 1}
            ).limit(5))
            with results_lock:
                results.append(("read", "success", time.time()))
        except Exception:
            with results_lock:
                results.append(("read", "failure", time.time()))
        i += 1
        time.sleep(0.2)
    client.close()

def aggregation_thread():
    """Continuously runs aggregations — simulates analytics dashboard"""
    client = pymongo.MongoClient(
        REPLICA_SET_URI,
        directConnection=False,
        serverSelectionTimeoutMS=3000,
        socketTimeoutMS=3000
    )
    collection = client["mongoguard"]["load_test"]
    while not stop_all.is_set():
        try:
            list(collection.aggregate([
                {"$group": {
                    "_id": "$product",
                    "avg_price": {"$avg": "$price"},
                    "total": {"$sum": 1}
                }}
            ]))
            with results_lock:
                results.append(("aggregation", "success", time.time()))
        except Exception:
            with results_lock:
                results.append(("aggregation", "failure", time.time()))
        time.sleep(1.0)
    client.close()

# Step 4: Start all three workload types simultaneously
print("\nStep 4: Starting realistic mixed workload...")
print("  Thread 1: continuous writes (new orders)")
print("  Thread 2: continuous reads (user browsing)")
print("  Thread 3: continuous aggregations (analytics)")

t_write = threading.Thread(target=writer_thread, args=(PRIMARY_PUBLIC,))
t_read  = threading.Thread(target=reader_thread)
t_agg   = threading.Thread(target=aggregation_thread)

t_write.daemon = True
t_read.daemon  = True
t_agg.daemon   = True

t_write.start()
t_read.start()
t_agg.start()

print("\nStep 5: Establishing baseline for 10 seconds...")
time.sleep(10)

with results_lock:
    baseline_writes = sum(1 for r in results if r[0]=="write" and r[1]=="success")
    baseline_reads  = sum(1 for r in results if r[0]=="read"  and r[1]=="success")
    baseline_aggs   = sum(1 for r in results if r[0]=="aggregation" and r[1]=="success")

print(f"  Baseline writes      : {baseline_writes}")
print(f"  Baseline reads       : {baseline_reads}")
print(f"  Baseline aggregations: {baseline_aggs}")

# Step 6: Kill primary while load is running
print(f"\nStep 6: Killing MongoDB on primary ({PRIMARY_PUBLIC})...")
print("  Mixed load continues running during election...")
try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=PRIMARY_PUBLIC,
        username=SSH_USER,
        key_filename=KEY_FILE,
        timeout=30
    )
    ssh.exec_command(
        "sudo kill -9 $(sudo pgrep mongod) ; sudo systemctl mask mongod"
    )
    kill_time[0] = time.time()
    print(f"  MongoDB killed at t=0")
    ssh.close()
except Exception as e:
    print(f"  SSH failed: {e}")
    stop_all.set()

# Step 7: Monitor for 60 seconds
print("\nStep 7: Monitoring all operation types for 60 seconds...")
print(f"  {'Time':>5}  {'W-fail':>8}  {'R-fail':>8}  {'A-fail':>8}  {'W-ok':>6}  {'R-ok':>6}")

for i in range(6):
    time.sleep(10)
    elapsed = (i + 1) * 10
    kt = kill_time[0]
    with results_lock:
        wf = sum(1 for r in results if r[0]=="write"       and r[1]=="failure" and r[2] > kt)
        rf = sum(1 for r in results if r[0]=="read"        and r[1]=="failure" and r[2] > kt)
        af = sum(1 for r in results if r[0]=="aggregation" and r[1]=="failure" and r[2] > kt)
        ws = sum(1 for r in results if r[0]=="write"       and r[1]=="success" and r[2] > kt)
        rs = sum(1 for r in results if r[0]=="read"        and r[1]=="success" and r[2] > kt)
    print(f"  t={elapsed:>3}s  {wf:>8}  {rf:>8}  {af:>8}  {ws:>6}  {rs:>6}")

# Step 8: Stop all threads
stop_all.set()
t_write.join(timeout=5)
t_read.join(timeout=5)
t_agg.join(timeout=5)

# Step 9: Restart MongoDB
print("\nStep 8: Restarting MongoDB on primary...")
try:
    ssh2 = paramiko.SSHClient()
    ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh2.connect(
        hostname=PRIMARY_PUBLIC,
        username=SSH_USER,
        key_filename=KEY_FILE,
        timeout=30
    )
    ssh2.exec_command(
        "sudo systemctl unmask mongod && sudo systemctl start mongod"
    )
    ssh2.close()
    print("  MongoDB restarted — will rejoin as secondary")
except Exception as e:
    print(f"  Could not restart: {e}")

# Step 10: Analyze
kt = kill_time[0]
write_failures   = [r for r in results if r[0]=="write"       and r[1]=="failure" and r[2] > kt]
read_failures    = [r for r in results if r[0]=="read"        and r[1]=="failure" and r[2] > kt]
agg_failures     = [r for r in results if r[0]=="aggregation" and r[1]=="failure" and r[2] > kt]
write_recoveries = [r for r in results if r[0]=="write"       and r[1]=="success" and r[2] > kt]

first_write_fail = write_failures[0][2]   if write_failures   else None
first_read_fail  = read_failures[0][2]    if read_failures    else None
first_write_ok   = write_recoveries[0][2] if write_recoveries else None

print()
print("=" * 55)
print("MEASUREMENT 7 RESULTS")
print("=" * 55)
print()
print(f"Total write failures      : {len(write_failures)}")
print(f"Total read failures       : {len(read_failures)}")
print(f"Total aggregation failures: {len(agg_failures)}")
print()

if first_write_fail:
    print(f"Write failure started at  : t={round(first_write_fail - kt, 2)}s after kill")
if first_read_fail:
    print(f"Read failure started at   : t={round(first_read_fail - kt, 2)}s after kill")
if first_write_ok:
    print(f"Write recovery detected at: t={round(first_write_ok - kt, 2)}s after kill")
    if first_write_fail:
        election_time = round(first_write_ok - first_write_fail, 2)
        print(f"Election time under load  : {election_time}s")

print()
print("COMPARISON TO MEASUREMENT 3 (inserts only):")
print("  Measurement 3 election time: ~65s (t3.micro)")
print(f"  Measurement 7 election time: see above")
print()
print("WHAT THIS MEANS:")
print("Reads fail differently from writes during election.")
print("Writes fail immediately when primary dies.")
print("Reads on secondaries may continue working")
print("because secondaries stay up during election.")
print("Aggregations behave like reads if routed to secondaries.")
print()
print("ENGINEERING DECISION:")
print("Design your application with operation-specific")
print("retry logic. Writes need longer retry windows.")
print("Reads can often be served from secondaries during")
print("election with stale data rather than failing entirely.")