import pymongo
import time

def setup_replica_set():
    print("Connecting to mongo-primary...")
    client = pymongo.MongoClient("mongodb://127.0.0.1:27017/")

    print("Initiating replica set rs0...")
    config = {
        "_id": "rs0",
        "members": [
            {"_id": 0, "host": "127.0.0.1:27017", "priority": 2},
            {"_id": 1, "host": "127.0.0.1:27018", "priority": 1},
            {"_id": 2, "host": "127.0.0.1:27019", "priority": 1}
        ]
    }

    try:
        result = client.admin.command("replSetInitiate", config)
        print(f"Replica set initiated: {result}")
    except Exception as e:
        print(f"Note: {e}")

    print("\nWaiting 15 seconds for election to complete...")
    time.sleep(15)

    print("\nChecking replica set status...")
    status = client.admin.command("replSetGetStatus")
    print("\nReplica Set Members:")
    for member in status["members"]:
        print(f"  {member['name']}: {member['stateStr']}")

    client.close()
    print("\nReplica set is ready.")

if __name__ == "__main__":
    setup_replica_set()