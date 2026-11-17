# Live checks against Rook-Ceph RGW from inside the cluster (Job in the app namespace, backend image + boto3). PHASE=app|es.
import json
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

EP = os.environ["S3_ENDPOINT"]
BK = json.loads(os.environ["BUCKETS"])  # {"app":..,"pg":..,"es":..}
fails = []


def client(ak, sk):
    return boto3.client("s3", endpoint_url=EP, aws_access_key_id=ak, aws_secret_access_key=sk, region_name="us-east-1",
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(detail) if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def code(fn):
    try:
        fn()
        return "OK"
    except ClientError as e:
        return e.response["Error"]["Code"]


app = client(os.environ["APP_AK"], os.environ["APP_SK"])
pg = client(os.environ["PG_AK"], os.environ["PG_SK"])
es = client(os.environ["ES_AK"], os.environ["ES_SK"])
adm = client(os.environ["ADMIN_AK"], os.environ["ADMIN_SK"])

if os.environ["PHASE"] == "app":
    check("app: put/get/delete in app bucket", code(lambda: (app.put_object(Bucket=BK["app"], Key="tmp/rehearsal", Body=b"x"),
          app.get_object(Bucket=BK["app"], Key="tmp/rehearsal"), app.delete_object(Bucket=BK["app"], Key="tmp/rehearsal"))) == "OK")
    rules = [r["ID"] for r in app.get_bucket_lifecycle_configuration(Bucket=BK["app"])["Rules"]]
    check("app bucket lifecycle has tmp-expire", "tmp-expire" in rules, rules)
    for k in ("pg", "es"):
        c = code(lambda: app.list_objects_v2(Bucket=BK[k]))
        check("app key denied on %s backup bucket" % k, c in ("AccessDenied", "403"), c)
        olc = adm.get_object_lock_configuration(Bucket=BK[k])["ObjectLockConfiguration"]
        check("object lock on %s bucket" % k, olc.get("ObjectLockEnabled") == "Enabled" and olc["Rule"]["DefaultRetention"]["Days"] == 30, olc)
    for k, w in (("pg", pg), ("es", es)):
        key = "rehearsal/%s" % k
        check("%s writer put" % k, code(lambda: w.put_object(Bucket=BK[k], Key=key, Body=b"backup")) == "OK")
        ver = w.list_object_versions(Bucket=BK[k], Prefix=key)["Versions"][0]["VersionId"]
        c = code(lambda: w.delete_object(Bucket=BK[k], Key=key, VersionId=ver))
        check("%s writer delete locked version denied" % k, c in ("AccessDenied", "403"), c)
        c = code(lambda: w.delete_object(Bucket=BK[k], Key=key, VersionId=ver, BypassGovernanceRetention=True))
        check("%s writer governance bypass denied" % k, c in ("AccessDenied", "403"), c)
        check("%s writer delete (marker) allowed" % k, code(lambda: w.delete_object(Bucket=BK[k], Key=key)) == "OK")
        c = code(lambda: app.delete_object(Bucket=BK[k], Key=key, VersionId=ver))
        check("%s app key delete version denied" % k, c in ("AccessDenied", "403"), c)
else:
    r = adm.list_object_versions(Bucket=BK["es"])
    check("es bucket has objects", len(r.get("Versions", [])) > 0, len(r.get("Versions", [])))
    check("es bucket has delete markers or noncurrent versions after snapshot delete",
          len(r.get("DeleteMarkers", [])) > 0 or any(not v["IsLatest"] for v in r.get("Versions", [])))
    r = adm.list_object_versions(Bucket=BK["pg"])
    check("pg bucket has WAL/base backup objects", len(r.get("Versions", [])) > 1, len(r.get("Versions", [])))
sys.exit(1 if fails else 0)
