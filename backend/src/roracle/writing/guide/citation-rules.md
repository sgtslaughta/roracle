# Citation rules

- Keys: `@doc:<document_uuid>` cites a whole library document; `@psg:<version_uuid>:<n>` cites one passage
  (chunk id `<version_id>:<n>` from `get_passages`). Group them: `[@doc:...; @psg:...:12]`.
- Cite only what you have read. A key that does not exist and one you may not read look identical
  ("unresolved"); both block submission.
- Each citation is judged against the passage it points to (or the best matching passage of the document):
  `supported`, `unsupported` or `unclear`. Unsupported and unclear citations block submission until you fix the
  sentence or the author acknowledges them with a reason.
- Quote sparingly and mark quotations; paraphrase and cite.
- Acknowledgements are tied to the exact sentence: editing the sentence clears them.
- Numeric or author-year labels follow the profile's `citation_style`; the References section is generated.
