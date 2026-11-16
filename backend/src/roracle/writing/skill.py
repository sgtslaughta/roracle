"""One source (`guide/*.md` + effective profiles) for the MCP prompt, MCP resources and the skill zip."""

import io
import json
import zipfile
from importlib import resources

from sqlalchemy.orm import Session

from roracle.writing import profiles

SKILL = "roracle-writer"
_DESC = (
    "Write a research paper as a rOracle draft: search the library, cite passages, validate against a "
    "writing profile, verify citations, then hand the draft to its owner for submission."
)
_DATE = (2026, 1, 1, 0, 0, 0)  # fixed: the zip is reproducible


def guide(name: str) -> str:
    return resources.files(__package__).joinpath("guide", f"{name}.md").read_text(encoding="utf-8")


def profile_list(session: Session) -> str:
    return json.dumps(profiles.list_effective(session))


def profile_yaml(session: Session, name: str) -> str:
    return profiles.yaml_text(session, name)


def prompt_text(session: Session, topic: str, profile: str = profiles.DEFAULT_PROFILE) -> str:
    """User message of the `write_paper` prompt (KeyError for an unknown profile)."""
    prof, _, _ = profiles._named(session, profile)
    return (
        f"{guide('write-paper')}\n## Topic\n\n{topic}\n\n## Profile: {prof.name} ({prof.title})\n\n"
        f"Required sections: {', '.join(prof.required_sections) or 'none'}\n"
        f"Required metadata: {', '.join(prof.required_metadata) or 'none'}\n\n"
        f"Style guidance:\n{prof.style_guidance}\nTemplate:\n\n```markdown\n{prof.template}\n```\n"
    )


def skill_md() -> str:
    return f"---\nname: {SKILL}\ndescription: {_DESC}\n---\n\n{guide('write-paper')}"


def zip_bytes(session: Session) -> bytes:
    files = {
        f"{SKILL}/SKILL.md": skill_md(),
        f"{SKILL}/references/style-guide.md": guide("style-guide"),
        f"{SKILL}/references/citation-rules.md": guide("citation-rules"),
    }
    for e in profiles.list_effective(session):
        files[f"{SKILL}/profiles/{e['name']}.yaml"] = profile_yaml(session, e["name"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files.items():
            z.writestr(zipfile.ZipInfo(path, _DATE), text)
    return buf.getvalue()
