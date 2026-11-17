# Writing profiles

A writing profile tells the [draft editor](../user/writing-drafts.md) what a document of one kind must look like: the template a draft starts from, the sections and front-matter fields it needs, its limits, which checks block and whether a person must review it before it is published. rOracle ships seven profiles. You can replace any of them or add your own, as YAML, through the admin API. There is no admin screen and no CLI command for profiles. Terms are in the [glossary](../glossary.md).

Shipped profiles: `journal-article` (the default), `conference-paper`, `dissertation`, `grant-proposal`, `literature-review`, `preregistration` and `technical-report`.

## Add or replace a profile

`POST /api/admin/writing-profiles` takes `{"yaml": "<the profile as text>"}` and answers `201` with the profile `name` and its new `version`. A bad profile is refused with `422` and the first problem, for example `limits.words_max: Input should be greater than or equal to 1`. Nothing is stored in that case.

Versioning works by name:

- A profile is identified by its `name`. Saving a name again adds a new version; old versions are kept, and the newest active one is used.
- A shipped profile counts as version 0, so the first override of `journal-article` is version 1. The shipped copy is untouched and reappears if you deactivate the override.
- Saving is audited as `admin.writing_profile` with the name and version.

Other calls:

| Call | Does |
|---|---|
| `GET /api/admin/writing-profiles` | Lists every effective profile: `name`, `title`, `version` and `source` (`builtin` or `db`) |
| `GET /api/admin/writing-profiles/{name}` | Returns each stored version with its YAML, plus the shipped YAML as version 0. `404` for an unknown name |
| `DELETE /api/admin/writing-profiles/{name}` | Deactivates every stored version of that name. The shipped profile of that name, if any, applies again. `404` if nothing was active |

A deactivation is audited as `admin.writing_profile` with `deactivated: true`. A draft that stored the name of a profile that no longer exists is matched again automatically, as if it had none.

## The YAML schema

Unknown keys are refused. The file must be a YAML mapping of at most 64 KiB.

| Key | Required | Type and limits | Meaning |
|---|---|---|---|
| `name` | yes | `a-z`, `0-9`, `-`, 1 to 60 characters | The identifier |
| `title` | yes | text | The name shown to authors |
| `doc_types` | no | list of text | Values of the draft's `doc_type` front-matter field this profile fits |
| `org_units` | no | list of org-unit paths | Where the profile applies. Each must start with `/`; repeated slashes and a trailing slash are tidied |
| `template` | no | text | What a new draft starts with (usually front matter and section headings) |
| `required_sections` | no | list of headings | Sections that must exist, compared without regard to case |
| `required_metadata` | no | list of front-matter keys | Fields that must be present and not empty |
| `limits` | no | numbers of at least 1: `words_max`, `abstract_words_max`, `title_chars_max`, `figures_max`, `references_max` | Upper limits; leave one out for no limit. `references_max` counts distinct cited papers |
| `citation_style` | no | `numeric` (default) or `author-year` | How citations appear in the built PDF |
| `acronym_allowlist` | no | list of text | Acronyms the acronym check never reports |
| `style_guidance` | no | text | Instructions the language-model style review checks the text against |
| `review_required` | no | `true` or `false` (default) | A person must approve before publishing |
| `default_label` | no | label name, up to 40 characters | The minimum sensitivity label for the published paper |
| `rules` | no | rule name to `blocking`, `warning` or `off` | Severity per check |

### Rules

Each check has a default severity, and `rules` overrides it. In YAML a bare `off` is accepted.

| Rule | Checks | Default |
|---|---|---|
| `structure` | Every `required_sections` heading exists | blocking |
| `limits` | The numbers in `limits` are not exceeded | blocking |
| `metadata` | The front matter parses and `required_metadata` is filled | blocking |
| `notation` | A math symbol is defined where it is first used | warning |
| `acronyms` | An acronym is defined at first use | warning |
| `crossrefs` | No duplicate label, and every `@` reference has a label | blocking |
| `alt_text` | Every image has alt text | blocking |
| `si_units` | No non-SI or ambiguous units, a space between number and unit | warning |
| `ai_disclosure` | The front matter has an `ai_assistance` block with `used` set, and a `description` when AI was used | blocking |
| `citations` | Each citation is well formed and resolves to a paper the checking author can see | blocking |
| `style` | The language-model style review | warning |

Things to know:

- At most 100 findings are reported per rule (50 for `notation` and `acronyms`). At most `ROR_WRITING_MAX_CITATIONS` (default 200) distinct citations are checked, and a draft with more is blocked whatever the severity of `citations`, unless that rule is `off`.
- Validation also stops with a blocking finding if the deterministic checks take longer than `ROR_WRITING_VALIDATE_BUDGET_S` (default 10 seconds).
- `style` never blocks: if the review is unavailable it is reported as a warning only. It is skipped, with a warning, when the draft's label has the `exclude_llm` gate, because the text must not reach a model. The model is `ROR_WRITING_MODEL`, or the first chat model if that is empty.
- `ai_disclosure` blocks unless the draft's front matter has the block. The shipped templates include it, so a custom profile should too: leave the template empty and every new draft starts out failing this check.

## Which profile a draft gets

1. The profile the author chose when starting the draft, if it still exists.
2. Otherwise the newest admin-defined profile whose `doc_types` contains the draft's `doc_type` front-matter field and whose `org_units` contains the draft's org unit or one of its parents (`/` matches everywhere). Profiles you saved with no `org_units` never match this way.
3. Otherwise `journal-article`.

Shipped profiles take part in step 2 only after you have saved a version of them; until then they are used when chosen by name or as the default.

## Review and labels

- **Review.** A draft goes to a reviewer if its own profile has `review_required: true`, or if any active profile with `review_required: true` is scoped to the draft's org unit (or a parent), or to the submitter's org units, whatever profile the author chose. There is no way to opt out. `dissertation` ships with it on.
- **Label.** On submit the paper's label becomes the highest of its current label, the one the author requested and the profile's `default_label`. Submitting never lowers a label. Names are matched without regard to case. A name that is not an existing [label](labels.md) is ignored, and validation warns about it.

## Example

```yaml
name: lab-report
title: Lab report
doc_types: [lab-report]
org_units: [/Physics]
citation_style: numeric
required_sections: [Summary, Method, Results]
required_metadata: [title, authors]
limits:
  words_max: 3000
  figures_max: 12
review_required: true
default_label: Internal      # must match an existing label name
acronym_allowlist: [SI, CERN]
style_guidance: |
  Keep each paragraph to one idea. State uncertainties next to the numbers.
rules:
  style: off
  si_units: blocking
template: |
  ---
  title: ""
  doc_type: lab-report
  authors: []
  ai_assistance:
    used: false
    description: ""
  ---

  ## Summary

  ## Method

  ## Results
```

## Related

- [Writing drafts](../user/writing-drafts.md): what authors see.
- [Sensitivity labels](labels.md): the labels `default_label` can name.
- [Audit log](audit.md#what-is-recorded): `admin.writing_profile`.
