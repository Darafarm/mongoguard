import boto3
import time

# AWS configuration
REGION = "us-east-1"
INSTANCE_TYPE = "t3.micro"  # Free tier eligible
MONGO_VERSION = "6.0"

ec2 = boto3.resource("ec2", region_name=REGION)
ec2_client = boto3.client("ec2", region_name=REGION)

print("=" * 55)
print("MongoGuard AWS Setup")
print("=" * 55)
print(f"Region       : {REGION}")
print(f"Instance type: {INSTANCE_TYPE}")
print()

# Step 1: Create key pair for SSH access
print("Step 1: Creating SSH key pair...")
try:
    key_pair = ec2_client.create_key_pair(KeyName="mongoguard-key")
    with open("mongoguard-key.pem", "w") as f:
        f.write(key_pair["KeyMaterial"])
    print("  Key pair created: mongoguard-key.pem")
    print("  IMPORTANT: Keep this file safe — you need it to SSH into instances")
except Exception as e:
    if "InvalidKeyPair.Duplicate" in str(e):
        print("  Key pair already exists — continuing")
    else:
        raise e

# Step 2: Create VPC
print("\nStep 2: Creating VPC...")
vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
vpc.create_tags(Tags=[{"Key": "Name", "Value": "mongoguard-vpc"}])
vpc.modify_attribute(EnableDnsHostnames={"Value": True})
vpc.modify_attribute(EnableDnsSupport={"Value": True})
print(f"  VPC created: {vpc.id}")

# Step 3: Create internet gateway
print("\nStep 3: Creating internet gateway...")
igw = ec2.create_internet_gateway()
vpc.attach_internet_gateway(InternetGatewayId=igw.id)
print(f"  Internet gateway: {igw.id}")

# Step 4: Create subnet
print("\nStep 4: Creating subnet...")
subnet = ec2.create_subnet(
    CidrBlock="10.0.1.0/24",
    VpcId=vpc.id,
    AvailabilityZone=f"{REGION}a"
)
subnet.create_tags(Tags=[{"Key": "Name", "Value": "mongoguard-subnet"}])
ec2_client.modify_subnet_attribute(
    SubnetId=subnet.id,
    MapPublicIpOnLaunch={"Value": True}
)
print(f"  Subnet created: {subnet.id}")

# Step 5: Configure routing
print("\nStep 5: Configuring routing...")
route_table = vpc.create_route_table()
route_table.create_route(
    DestinationCidrBlock="0.0.0.0/0",
    GatewayId=igw.id
)
route_table.associate_with_subnet(SubnetId=subnet.id)
print("  Route table configured")

# Step 6: Create security group
print("\nStep 6: Creating security group...")
sg = ec2.create_security_group(
    GroupName="mongoguard-sg",
    Description="MongoGuard MongoDB replica set",
    VpcId=vpc.id
)
sg.create_tags(Tags=[{"Key": "Name", "Value": "mongoguard-sg"}])

# Allow SSH from anywhere
sg.authorize_ingress(IpPermissions=[{
    "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
    "IpRanges": [{"CidrIp": "0.0.0.0/0"}]
}])

# Allow MongoDB between instances
sg.authorize_ingress(IpPermissions=[{
    "IpProtocol": "tcp", "FromPort": 27017, "ToPort": 27017,
    "IpRanges": [{"CidrIp": "10.0.0.0/16"}]
}])

# Allow MongoDB from your laptop
sg.authorize_ingress(IpPermissions=[{
    "IpProtocol": "tcp", "FromPort": 27017, "ToPort": 27017,
    "IpRanges": [{"CidrIp": "0.0.0.0/0"}]
}])
print(f"  Security group: {sg.id}")

# Step 7: Launch three EC2 instances
print("\nStep 7: Launching three EC2 instances...")

# Amazon Linux 2023 AMI for us-east-1
AMI_ID = "ami-0182f373e66f89c85"

# MongoDB installation script runs automatically when instance starts
user_data = """#!/bin/bash
# Install MongoDB 6.0
cat > /etc/yum.repos.d/mongodb-org-6.0.repo << 'EOF'
[mongodb-org-6.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/amazon/2023/mongodb-org/6.0/x86_64/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-6.0.asc
EOF
yum install -y mongodb-org
systemctl enable mongod
"""

instance_names = ["mongoguard-primary", "mongoguard-secondary1", "mongoguard-secondary2"]
instances = []

for name in instance_names:
    instance = ec2.create_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        KeyName="mongoguard-key",
        UserData=user_data,
        NetworkInterfaces=[{
            "SubnetId": subnet.id,
            "DeviceIndex": 0,
            "AssociatePublicIpAddress": True,
            "Groups": [sg.id]
        }],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": name}]
        }]
    )[0]
    instances.append(instance)
    print(f"  Launched: {name} ({instance.id})")

# Step 8: Wait for all instances to be running
print("\nStep 8: Waiting for instances to start (2 to 3 minutes)...")
for instance in instances:
    instance.wait_until_running()
    instance.reload()

print("\n" + "=" * 55)
print("INSTANCES READY")
print("=" * 55)

# Save instance details to file
with open("aws_instances.txt", "w") as f:
    for i, (instance, name) in enumerate(zip(instances, instance_names)):
        line = f"{name}: {instance.id} | Public IP: {instance.public_ip_address} | Private IP: {instance.private_ip_address}"
        print(line)
        f.write(line + "\n")

print("\nInstance details saved to aws_instances.txt")
print("Next step: run setup_replica_aws.py to initialize the replica set")
print("\nNOTE: MongoDB is installing in background on each instance.")
print("Wait 3 minutes before running setup_replica_aws.py")