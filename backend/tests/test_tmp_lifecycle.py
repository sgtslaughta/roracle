import logging

from botocore.exceptions import ClientError

from roracle.storage import TMP_RULE_ID, BlobStore, ensure_tmp_lifecycle


class Client:
    def __init__(self, rules=None, get_err=None, put_err=None):
        self.rules, self.get_err, self.put_err, self.puts = rules, get_err, put_err, []

    def get_bucket_lifecycle_configuration(self, Bucket):
        if self.get_err:
            raise ClientError({"Error": {"Code": self.get_err}}, "Get")
        if self.rules is None:
            raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "Get")
        return {"Rules": self.rules}

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        if self.put_err:
            raise ClientError({"Error": {"Code": self.put_err}}, "Put")
        self.puts.append(LifecycleConfiguration["Rules"])


def test_adds_rule_keeping_others_and_is_idempotent():
    other = {
        "ID": "x",
        "Status": "Enabled",
        "Filter": {"Prefix": "blobs/"},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 9},
    }
    c = Client([other])
    assert ensure_tmp_lifecycle(BlobStore(c, "b")) == "added"
    (rules,) = c.puts
    assert rules[0] == other and rules[1]["ID"] == TMP_RULE_ID and rules[1]["Filter"] == {"Prefix": "tmp/"}
    assert ensure_tmp_lifecycle(BlobStore(Client(rules), "b")) == "present"
    assert ensure_tmp_lifecycle(BlobStore(Client(None), "b")) == "added"  # no configuration yet


def test_access_denied_is_a_warning_not_an_error(caplog):
    for c in (Client(None, put_err="AccessDenied"), Client(get_err="AccessDenied")):
        with caplog.at_level(logging.WARNING):
            assert ensure_tmp_lifecycle(BlobStore(c, "b")) == "skipped"
    assert "lifecycle" in caplog.text
