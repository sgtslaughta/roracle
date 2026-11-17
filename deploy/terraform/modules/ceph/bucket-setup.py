# Creates the PG backup / ES snapshot buckets with S3 object lock, default retention, lifecycle and a bucket policy.
# Idempotent. Runs as the admin object-store user (bucket owner); the backup writers get Put/Get/List/Delete(marker) but can neither
# delete object versions nor bypass governance retention, change the lock, the lifecycle or the policy. The app identity has no grant.
import json
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

days = int(os.environ["RETENTION_DAYS"])
mode = os.environ["RETENTION_MODE"]
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["ADMIN_AK"],
    aws_secret_access_key=os.environ["ADMIN_SK"],
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 8, "mode": "standard"}),
)
for b in json.loads(os.environ["BUCKETS"]):
    name, user = b["name"], b["user"]
    try:
        s3.create_bucket(Bucket=name, ObjectLockEnabledForBucket=True)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    s3.put_object_lock_configuration(
        Bucket=name,
        ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": mode, "Days": days}}},
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=name,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "noncurrent-and-multipart",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": days + 1},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }
            ]
        },
    )
    principal = {"AWS": ["arn:aws:iam:::user/" + user]}
    resources = ["arn:aws:s3:::" + name, "arn:aws:s3:::" + name + "/*"]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "writer",
                "Effect": "Allow",
                "Principal": principal,
                "Action": [
                    "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
                    "s3:ListBucketVersions", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads",
                    "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload", "s3:GetObjectRetention",
                ],
                "Resource": resources,
            },
            {
                "Sid": "no-lock-bypass",
                "Effect": "Deny",
                "Principal": principal,
                "Action": [
                    "s3:BypassGovernanceRetention", "s3:PutObjectRetention", "s3:DeleteObjectVersion",
                    "s3:PutBucketObjectLockConfiguration", "s3:PutLifecycleConfiguration",
                    "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:DeleteBucket",
                ],
                "Resource": resources,
            },
        ],
    }
    s3.put_bucket_policy(Bucket=name, Policy=json.dumps(policy))
    print("bucket ready: %s (object lock %s %sd, writer %s)" % (name, mode, days, user), flush=True)
sys.exit(0)
