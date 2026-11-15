"""LLM summary for the search index."""

SYSTEM_PROMPT = "Summarize this research document in at most 3 sentences for a search index. Plain text."
INPUT_CHARS = 6000
MAX_SUMMARY_CHARS = 1000


def summarize(client, model: str, title: str, text: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{title}\n\n{text[:INPUT_CHARS]}"},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()[:MAX_SUMMARY_CHARS]
