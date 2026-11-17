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
