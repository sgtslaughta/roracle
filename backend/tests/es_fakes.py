"""Shared fake-ES helper: `msearch` that delegates each sub-search to the fake's own `search(**kw)`."""


def msearch(self, index=None, searches=()):
    """`es.msearch(index=..., searches=[{}, body, {}, body, ...])` -> responses from `self.search`."""
    responses = []
    for body in list(searches)[1::2]:
        kw = {("source" if k == "_source" else k): v for k, v in body.items()}
        try:
            responses.append(self.search(index=index, **kw))
        except Exception as e:
            if getattr(e, "meta", None) is None:
                raise  # transport failures fail the whole call, as in the real client
            responses.append({"error": {"reason": str(e)}, "status": e.meta.status})  # ES: reported in place
    return {"responses": responses}


class NullEs:
    """ES that accepts every ACL sync / mapping call and answers nothing: for route tests that only need the PATCH
    path to complete (they must not depend on whatever Elasticsearch happens to run on the default URL)."""

    def __init__(self):
        from types import SimpleNamespace

        self.indices = SimpleNamespace(put_mapping=lambda **kw: {"acknowledged": True})
        self.calls = []

    def update_by_query(self, **kw):
        self.calls.append(kw)
        return {"updated": 0, "version_conflicts": 0}

    def search(self, **kw):
        return {"hits": {"hits": []}}

    msearch = msearch
