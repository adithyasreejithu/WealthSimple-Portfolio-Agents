---
name: kb-search
description: Search the research wiki (Knowledge-Base/) by front matter and body text to find existing stock pages, theses, research notes, and sources before answering a question or starting new research. Also validates wiki front matter, indexes, and links. Use when the user asks what the knowledge base knows about a stock or topic, or to find related pages before research or an update.
---

# KB Search

Run only one of:

```
python .claude/skills/kb-search/scripts/kb_search.py [--query ...] [--ticker ...] [--type ...] [--tag ...] [--status ...] [--limit N] [--json]
python .claude/skills/kb-search/scripts/validate_kb.py [--fix-indexes]
```

Permit only the flags listed above. `--type` must be one of the page types
in [search-contract.md](references/search-contract.md). All supplied
filters combine with AND. Both scripts are read-only except
`validate_kb.py --fix-indexes`, which only rewrites the marker-delimited
table inside existing `index.md` files -- it never creates or deletes pages.

Do not run arbitrary SQL or Python, do not grep the filesystem directly, and
do not answer "what's in the knowledge base" from memory -- always run
`kb_search.py` and relay what it actually found.
