import paramiko
import time

# Your AWS instance details from aws_instances.txt
PRIMARY_IP = "98.81.54.92"
SECONDARY1_IP = "44.205.7.220"
SECONDARY2_IP = "3.235.19.128"

PRIMARY_PRIVATE = "10.0.1.8"
SECONDARY1_PRIVATE = "10.0.1.126"
SECONDARY2_PRIVATE = "10.0.1.195"

KEY_FILE = "mongoguard-key.pem"
SSH_USER = "ec2-user"

print("=" * 55)
print("MongoGuard Replica Set Setup on AWS")
print("=" * 55)
print()

def ssh_connect(ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=ip,
        username=SSH_USER,
        key_filename=KEY_FILE,
        timeout=30
    )
    return client

def run_command(client, command):
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode().strip()
    error = stderr.read().decode().strip()
    return output, error

# Step 1: Verify MongoDB is running on all three instances
print("Step 1: Verifying MongoDB is running on all instances...")
for name, ip in [
    ("primary", PRIMARY_IP),
    ("secondary1", SECONDARY1_IP),
    ("secondary2", SECONDARY2_IP)
]:
    try:
        client = ssh_connect(ip)
        output, error = run_command(client, "systemctl is-active mongod")
        if output == "active":
            print(f"  {name} ({ip}): MongoDB running")
        else:
            print(f"  {name} ({ip}): MongoDB not active yet — starting...")
            run_command(client, "sudo systemctl start mongod")
            time.sleep(5)
            output, _ = run_command(client, "systemctl is-active mongod")
            print(f"  {name} ({ip}): {output}")
        client.close()
    except Exception as e:
        print(f"  {name} ({ip}): Connection failed — {e}")

print()

# Step 2: Configure MongoDB on each instance for replica set
print("Step 2: Configuring MongoDB for replica set on all instances...")

mongo_config = """
sudo sed -i 's/#replication:/replication:\\n  replSetName: "rs0"/' /etc/mongod.conf
sudo sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/' /etc/mongod.conf
sudo systemctl restart mongod
"""

for name, ip in [
    ("primary", PRIMARY_IP),
    ("secondary1", SECONDARY1_IP),
    ("secondary2", SECONDARY2_IP)
]:
    client = ssh_connect(ip)
    run_command(client, mongo_config)
    print(f"  {name}: configured and restarted")
    client.close()

print("  Waiting 10 seconds for MongoDB to restart...")
time.sleep(10)

# Step 3: Initialize replica set from primary
print()
print("Step 3: Initializing replica set rs0...")

init_command = f"""
mongosh --eval "rs.initiate({{
  _id: 'rs0',
  members: [
    {{_id: 0, host: '{PRIMARY_PRIVATE}:27017', priority: 2}},
    {{_id: 1, host: '{SECONDARY1_PRIVATE}:27017', priority: 1}},
    {{_id: 2, host: '{SECONDARY2_PRIVATE}:27017', priority: 1}}
  ]
}})"
"""

client = ssh_connect(PRIMARY_IP)
output, error = run_command(client, init_command)
print(f"  Result: {output}")
client.close()

print("  Waiting 20 seconds for election to complete...")
time.sleep(20)

# Step 4: Verify replica set status
print()
print("Step 4: Verifying replica set status...")

client = ssh_connect(PRIMARY_IP)
output, _ = run_command(
    client,
    'mongosh --eval "rs.status().members.forEach(m => print(m.name + \': \' + m.stateStr))"'
)
print(output)
client.close()

print()
print("=" * 55)
print("REPLICA SET READY ON AWS")
print("=" * 55)
print(f"Primary   : {PRIMARY_IP}:27017 (private: {PRIMARY_PRIVATE})")
print(f"Secondary1: {SECONDARY1_IP}:27017 (private: {SECONDARY1_PRIVATE})")
print(f"Secondary2: {SECONDARY2_IP}:27017 (private: {SECONDARY2_PRIVATE})")
print()
print("Next step: run benchmark_write_concern.py")
print("Update connection string to use public IP:")
print(f"  mongodb://{PRIMARY_IP}:27017/?directConnection=true")