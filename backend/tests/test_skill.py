import io
import json
import zipfile

from mcp_helpers import rpc
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.models_writing import WritingProfile
from roracle.writing import profiles, skill

NAMES = sorted(profiles.builtin())


def zipped(client, h):
    r = client.get("/skills/roracle-writer.zip", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_zip_layout_and_frontmatter(client, as_ana):
    z = zipped(client, as_ana)
    assert sorted(z.namelist()) == sorted(
        ["roracle-writer/SKILL.md", "roracle-writer/references/style-guide.md",
         "roracle-writer/references/citation-rules.md"] + [f"roracle-writer/profiles/{n}.yaml" for n in NAMES]
    )  # fmt: skip
    md = z.read("roracle-writer/SKILL.md").decode()
    head, body = md.split("---\n", 2)[1:]
    assert (
        head.startswith("name: roracle-writer\ndescription: ")
        and body.strip() == skill.guide("write-paper").strip()
    )


def test_zip_requires_auth(client):
    assert client.get("/skills/roracle-writer.zip").status_code == 401


def test_zip_includes_db_override(client, as_ana, db_session):
    y = "name: journal-article\ntitle: Custom\n"
    db_session.add(
        WritingProfile(name="journal-article", version=1, yaml=y, spec=profiles.parse(y).model_dump())
    )
    db_session.commit()
    assert zipped(client, as_ana).read("roracle-writer/profiles/journal-article.yaml").decode() == y


def test_mcp_prompt_and_resources_match_zip(client, mc, ana, db_session, fes):  # noqa: F811
    h = pat(db_session, ana, ["read"])
    z = zipped(client, h)
    r = rpc(
        mc,
        h,
        "prompts/get",
        {"name": "write_paper", "arguments": {"topic": "graphs", "profile": "dissertation"}},
    )
    text = r.json()["result"]["messages"][0]["content"]["text"]
    assert text == skill.prompt_text(db_session, "graphs", "dissertation")
    assert text.startswith(z.read("roracle-writer/SKILL.md").decode().split("---\n", 2)[2].lstrip())
    assert "graphs" in text and "Required sections" in text

    def read(uri):
        return rpc(mc, h, "resources/read", {"uri": uri}).json()["result"]["contents"][0]["text"]

    assert (
        read("roracle://writing/style-guide") == z.read("roracle-writer/references/style-guide.md").decode()
    )
    assert (
        read("roracle://writing/citation-rules")
        == z.read("roracle-writer/references/citation-rules.md").decode()
    )
    assert (
        read("roracle://writing/profiles/dissertation")
        == z.read("roracle-writer/profiles/dissertation.yaml").decode()
    )
    assert [e["name"] for e in json.loads(read("roracle://writing/profiles"))] == NAMES
    bad = rpc(mc, h, "resources/read", {"uri": "roracle://writing/profiles/nope"}).json()
    assert "error" in bad or bad["result"].get("isError")
