import pymongo
import time
import threading

PRIMARY_PUBLIC = "98.81.54.92"
SECONDARY1_PUBLIC = "44.205.7.220"
SECONDARY2_PUBLIC = "3.235.19.128"

print("=" * 55)
print("MEASUREMENT 2: REPLICATION LAG — AWS")
print("=" * 55)
print("Question: After a heavy write burst, how long do")
print("secondaries take to catch up with the primary?")
print()

primary = pymongo.MongoClient(
    f"mongodb://{PRIMARY_PUBLIC}:27017/?directConnection=true",
    serverSelectionTimeoutMS=10000
)
secondary1 = pymongo.MongoClient(
    f"mongodb://{SECONDARY1_PUBLIC}:27017/?directConnection=true",
    serverSelectionTimeoutMS=10000
)
secondary2 = pymongo.MongoClient(
    f"mongodb://{SECONDARY2_PUBLIC}:27017/?directConnection=true",
    serverSelectionTimeoutMS=10000
)

test_scenarios = [
    ("Normal load",  1000),
    ("Peak load",    5000),
    ("Burst load",  10000),
]

all_results = []

for scenario_name, num_docs in test_scenarios:
    print(f"{'─' * 45}")
    print(f"Scenario: {scenario_name} ({num_docs:,} documents)")
    print(f"{'─' * 45}")

    docs = [{"_id": i, "data": f"record_{i}"} for i in range(num_docs)]

    primary["mongoguard"]["lag_test"].drop()
    time.sleep(2)

    # Shared state between threads
    insert_complete = threading.Event()
    insert_done_time = [None]
    s1_lag = [None]
    s2_lag = [None]

    def monitor_secondary(client, label, result_holder):
        """
        Thread function — monitors one secondary continuously.
        Starts BEFORE the insert begins so it catches lag
        from the very first document inserted.
        Uses threading concept from MIT 6.824 Lecture 2:
        IO concurrency — overlapping network waits.
        """
        while not insert_complete.is_set():
            try:
                count = client["mongoguard"]["lag_test"].count_documents({})
                if count >= num_docs:
                    result_holder[0] = round(
                        time.time() - insert_start_time[0], 2
                    )
                    print(f"  {label} caught up in {result_holder[0]}s")
                    return
            except Exception:
                pass
            time.sleep(0.05)

        # Insert finished — keep checking until caught up
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                count = client["mongoguard"]["lag_test"].count_documents({})
                if count >= num_docs:
                    result_holder[0] = round(
                        time.time() - insert_start_time[0], 2
                    )
                    print(f"  {label} caught up in {result_holder[0]}s")
                    return
            except Exception:
                pass
            time.sleep(0.05)

        result_holder[0] = 30
        print(f"  {label} did not catch up within 30s")

    insert_start_time = [None]

    # Start monitoring threads BEFORE inserting
    t1 = threading.Thread(
        target=monitor_secondary,
        args=(secondary1, "Secondary 1", s1_lag)
    )
    t2 = threading.Thread(
        target=monitor_secondary,
        args=(secondary2, "Secondary 2", s2_lag)
    )
    t1.start()
    t2.start()

    # Small delay so monitors are definitely running
    time.sleep(0.1)

    # Now insert — monitors are already watching
    print(f"Inserting {num_docs:,} documents to primary...")
    insert_start_time[0] = time.time()
    primary["mongoguard"]["lag_test"].insert_many(docs)
    insert_time = round(time.time() - insert_start_time[0], 3)
    insert_complete.set()
    print(f"Primary insert complete in {insert_time}s")
    print(f"Waiting for secondaries...")

    t1.join()
    t2.join()

    avg_lag = round(((s1_lag[0] or 0) + (s2_lag[0] or 0)) / 2, 2)
    replication_overhead = round(avg_lag - insert_time, 2)

    print(f"  Average lag: {avg_lag}s")
    print(f"  Replication overhead after insert: "
          f"{max(0, replication_overhead)}s")
    print()

    all_results.append({
        "scenario": scenario_name,
        "docs": num_docs,
        "insert_time": insert_time,
        "s1_lag": s1_lag[0],
        "s2_lag": s2_lag[0],
        "avg_lag": avg_lag,
        "overhead": max(0, replication_overhead)
    })

    time.sleep(5)

print("=" * 55)
print("REPLICATION LAG RESULTS SUMMARY")
print("=" * 55)
print(f"{'Scenario':<14} {'Docs':>8} {'Insert':>10} "
      f"{'Avg lag':>10} {'Overhead':>10}")
print("-" * 55)
for r in all_results:
    print(f"{r['scenario']:<14} {r['docs']:>8,} "
          f"{r['insert_time']:>9}s {r['avg_lag']:>9}s "
          f"{r['overhead']:>9}s")

print()
print("WHAT THESE NUMBERS MEAN:")
print()
for r in all_results:
    if r['overhead'] > 0:
        print(f"{r['scenario']}:")
        print(f"  Secondaries lagged {r['overhead']}s behind primary.")
        print(f"  During this window secondary reads returned stale data.")
    else:
        print(f"{r['scenario']}:")
        print(f"  Replication kept pace — no measurable lag window.")
    print()

primary.close()
secondary1.close()
secondary2.close()