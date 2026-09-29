"""Start an S3-compatible server on :9000 and create the Phase 1 buckets.

Data is kept in memory: it is lost when this container restarts (fine for local
testing; product images / invoices simply have to be uploaded again)."""
import os
import time

import boto3
from moto.server import ThreadedMotoServer

server = ThreadedMotoServer(ip_address="0.0.0.0", port=9000)
server.start()

s3 = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:9000",
    region_name="us-east-1",
    aws_access_key_id="local",
    aws_secret_access_key="local",
)
for bucket in filter(None, os.environ.get("BUCKETS", "").split(",")):
    s3.create_bucket(Bucket=bucket)
    if bucket != "ecommerce-images":
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    print(f"bucket ready: {bucket}", flush=True)

open("/tmp/ready", "w").write("ok")
print("local S3 listening on :9000", flush=True)
while True:
    time.sleep(3600)
