import sys
import pymongo
import paramiko
import time
import threading

ALL_PUBLIC_IPS = [
    "98.92.228.200",
    "100.58.155.129",
    "3.220.169.215"
]
KEY_FILE = "mongoguard-key.pem"
SSH_USER = "ec2-user"

write_results = []
results_lock = threading.Lock()
stop_writing = threading.Event()
kill_time = [None]

print("=" * 55)
print("MEASUREMENT 3: ELECTION TIME — AWS (kill -9)")
print("=" * 55)
print("Question: When the primary crashes instantly,")
print("how long until the cluster recovers?")
print()
print("Method: kill -9 simulates real production crash.")
print("MongoDB dies instantly — no graceful shutdown.")
print("This gives realistic election time measurement.")
print()

# Step 1: Auto-detect current primary
print("Step 1: Auto-detecting current primary...")
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
            print(f"  Primary found: {ip}")
        else:
            print(f"  {ip}: secondary")
        test.close()
    except Exception as e:
        print(f"  {ip}: not reachable ({str(e)[:50]})")

if not PRIMARY_PUBLIC:
    print("  No primary found. Check replica set status.")
    sys.exit(1)

SECONDARIES = [ip for ip in ALL_PUBLIC_IPS if ip != PRIMARY_PUBLIC]
REPLICA_SET_URI = (
    f"mongodb://{ALL_PUBLIC_IPS[0]}:27017,"
    f"{ALL_PUBLIC_IPS[1]}:27017,"
    f"{ALL_PUBLIC_IPS[2]}:27017/"
    f"?replicaSet=rs0&serverSelectionTimeoutMS=60000"
)

print(f"  Will crash MongoDB on: {PRIMARY_PUBLIC}")
print()

# Step 2: Clear test collection
print("Step 2: Clearing test collection...")
try:
    client = pymongo.MongoClient(
        REPLICA_SET_URI, directConnection=False
    )
    client["mongoguard"]["election_test"].drop()
    client.close()
    print("  Done.")
except Exception as e:
    print(f"  Failed: {e}")
    sys.exit(1)

print()

# Step 3: Define writer thread
def continuous_writer(primary_ip):
    """
    Connects DIRECTLY to primary only.
    When primary dies instantly (kill -9), this writer
    has nowhere to go — writes fail immediately.
    We record every failure with its timestamp so we
    can calculate exactly when the failure window started
    and when recovery completed.
    """
    writer_client = pymongo.MongoClient(
        f"mongodb://{primary_ip}:27017/?directConnection=true",
        serverSelectionTimeoutMS=3000,
        socketTimeoutMS=3000,
        connectTimeoutMS=3000
    )
    writer_col = writer_client["mongoguard"]["election_test"]
    i = 0
    while not stop_writing.is_set():
        try:
            writer_col.insert_one({"i": i, "ts": time.time()})
            with results_lock:
                write_results.append(("success", time.time()))
        except Exception:
            with results_lock:
                write_results.append(("failure", time.time()))
        i += 1
        time.sleep(0.2)
    writer_client.close()

# Step 4: Start writer and establish baseline
print("Step 3: Starting continuous writes directly to primary...")
writer_thread = threading.Thread(
    target=continuous_writer,
    args=(PRIMARY_PUBLIC,)
)
writer_thread.daemon = True
writer_thread.start()

print("Step 4: Establishing baseline for 8 seconds...")
time.sleep(8)
with results_lock:
    baseline = sum(1 for r in write_results if r[0] == "success")
print(f"  Baseline: {baseline} successful writes in 8 seconds")
print()

# Step 5: Kill MongoDB instantly using kill -9
# This simulates a real production crash accurately
# No graceful shutdown — MongoDB dies immediately
# Secondaries detect silence within 10 seconds
print(f"Step 5: Crashing MongoDB instantly on {PRIMARY_PUBLIC}...")
try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=PRIMARY_PUBLIC,
        username=SSH_USER,
        key_filename=KEY_FILE,
        timeout=30
    )
    print(f"  SSH connected.")
    print(f"  Sending kill -9 to MongoDB process...")

    # kill -9 $(pgrep mongod) — find MongoDB PID and kill instantly
    # mask prevents systemd from auto-restarting
    ssh.exec_command(
        "sudo kill -9 $(sudo pgrep mongod) ; "
        "sudo systemctl mask mongod"
    )
    kill_time[0] = time.time()
    print(f"  MongoDB killed instantly at t=0")
    print(f"  Masked to prevent auto-restart")
    ssh.close()
except Exception as e:
    print(f"  SSH failed: {e}")
    stop_writing.set()
    sys.exit(1)

print()
print("Step 6: Monitoring writes for 60 seconds...")
print("  Expecting failures within 10-15s as election runs")
print()

# Monitor for 60 seconds — enough for realistic election
for i in range(6):
    time.sleep(10)
    elapsed = (i + 1) * 10
    with results_lock:
        failures_so_far = sum(
            1 for r in write_results
            if r[0] == "failure" and r[1] > kill_time[0]
        )
        successes_after_kill = sum(
            1 for r in write_results
            if r[0] == "success" and r[1] > kill_time[0]
        )
        total_after_kill = failures_so_far + successes_after_kill
    print(f"  t={elapsed:>3}s — attempts: {total_after_kill:>3}  "
          f"failures: {failures_so_far:>3}  "
          f"successes: {successes_after_kill:>3}")

# Stop writer
stop_writing.set()
writer_thread.join(timeout=10)

# Step 7: Unmask and restart MongoDB
print()
print("Step 7: Restarting MongoDB on crashed server...")
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
        "sudo systemctl unmask mongod && "
        "sudo systemctl start mongod"
    )
    ssh2.close()
    print(f"  MongoDB restarted on {PRIMARY_PUBLIC}")
    print(f"  It will rejoin replica set as secondary.")
except Exception as e:
    print(f"  Could not restart: {e}")
    print(f"  Manually fix:")
    print(f"    ssh -i mongoguard-key.pem ec2-user@{PRIMARY_PUBLIC}")
    print(f"    sudo systemctl unmask mongod")
    print(f"    sudo systemctl start mongod")

# Step 8: Measure recovery using replica set connection
print()
print("Step 8: Measuring when new primary became available...")
recovery_start = kill_time[0]
new_primary = None
recovery_time = None

for attempt in range(60):
    try:
        rc = pymongo.MongoClient(
            REPLICA_SET_URI,
            directConnection=False,
            serverSelectionTimeoutMS=3000
        )
        hello = rc["mongoguard"].command("hello")
        primary_addr = hello.get("primary")
        if primary_addr and primary_addr != f"{PRIMARY_PUBLIC}:27017":
            new_primary = primary_addr
            recovery_time = round(time.time() - kill_time[0], 2)
            print(f"  New primary: {new_primary}")
            print(f"  Detected at t={recovery_time}s after kill")
            rc.close()
            break
        rc.close()
    except Exception:
        pass
    time.sleep(1)

if not recovery_time:
    print("  New primary not detected within 60s.")

# Step 9: Analyze
print()
kt = kill_time[0]
first_failure_time = None
first_recovery_time = None
failure_count = 0

for result in write_results:
    ts = result[1]
    status = result[0]
    if status == "failure" and ts > kt:
        if first_failure_time is None:
            first_failure_time = ts
        failure_count += 1
    elif (status == "success"
          and first_failure_time is not None
          and ts > first_failure_time
          and first_recovery_time is None):
        first_recovery_time = ts

total_writes = len(write_results)
success_count = sum(1 for r in write_results if r[0] == "success")

print("=" * 55)
print("MEASUREMENT 3 RESULTS (kill -9 hard crash)")
print("=" * 55)
print(f"Total write attempts : {total_writes}")
print(f"Successful writes    : {success_count}")
print(f"Failed writes        : {failure_count}")
print()

if first_failure_time:
    time_to_failure = round(first_failure_time - kt, 2)
    print(f"Time to first failure : {time_to_failure}s")
    print(f"  Secondaries detected silence after heartbeat timeout")
    print()

if recovery_time:
    if first_failure_time:
        election_time = round(recovery_time - time_to_failure, 2)
    else:
        election_time = recovery_time

    print(f"Election time         : {election_time}s")
    print(f"  From first failure to new primary available")
    print()
    print(f"Total downtime        : {recovery_time}s")
    print(f"  From kill command to new primary available")
    print()
    print(f"Write failures        : {failure_count}")
    print(f"  At 200ms intervals = ~{round(failure_count * 0.2, 1)}s of failures")
    print()
    print("WHAT THIS MEANS:")
    print(f"In a real production crash, your application")
    print(f"experiences approximately {election_time}s of")
    print(f"write failures while MongoDB runs Raft consensus")
    print(f"and elects a new primary.")
    print(f"Applications without retry logic lose {failure_count}")
    print(f"write attempts during this window.")
    print()
    print("ENGINEERING RECOMMENDATION:")
    if election_time < 15:
        print(f"Excellent — {election_time}s election time.")
        print("Design retry logic with 15s total timeout.")
        print("3 retries at 5 second intervals is sufficient.")
    elif election_time < 30:
        print(f"Good — {election_time}s election time.")
        print("Design retry logic with 30s total timeout.")
    else:
        print(f"Slow — {election_time}s election time.")
        print("Investigate replication lag on secondaries.")
        print("Lagged secondaries extend election time.")

elif first_failure_time:
    print(f"Failures detected at t={time_to_failure}s")
    print(f"Total failures: {failure_count}")
    print("New primary not confirmed in measurement window.")
    print("Check replica set:")
    print(f"  ssh -i mongoguard-key.pem ec2-user@{SECONDARIES[0]}")
    print(f"  mongosh --eval \"rs.status()\"")

else:
    print("No failures detected.")
    print("kill -9 may not have worked.")
    print("Check MongoDB on primary:")
    print(f"  ssh -i mongoguard-key.pem ec2-user@{PRIMARY_PUBLIC}")
    print(f"  sudo systemctl status mongod")

print()
print("=" * 55)
print("ALL RESULTS SO FAR")
print("=" * 55)
print("Measurement 1 — Write concern cost (AWS same-AZ):")
print("  w:1 average        : 1.033s")
print("  w:majority average : 1.012s")
print("  Overhead           : 2% — essentially free")
print()
print("Measurement 2 — Replication lag (AWS same-AZ):")
print("  Normal load  : 0.06s overhead after insert")
print("  Peak load    : 0.03s overhead after insert")
print("  Burst load   : 0.06s overhead after insert")
print("  Window       : 50-60ms regardless of volume")
print()
print("Measurement 3 — Election time (AWS same-AZ kill -9):")
if recovery_time and first_failure_time:
    print(f"  Time to first failure : {time_to_failure}s")
    print(f"  Election time         : {election_time}s")
    print(f"  Total downtime        : {recovery_time}s")
    print(f"  Write failures        : {failure_count}")
else:
    print("  See output above")