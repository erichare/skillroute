---
name: skillroute
description: Pick the right skills for a task from a large local skill library with SkillRoute's MCP tools. Use when the user asks which skill fits a request, when many installed skills could apply and you need a ranked shortlist with evidence, or when the user wants to index, search, or inspect their SKILL.md bundles.
---

# SkillRoute

SkillRoute keeps a local catalog of SKILL.md bundles and ranks them against a request. It offers three MCP tools:

- `skillroute.route`: pass the user's `request` (and the working directory as `repo`, when relevant). It returns ranked skills with a confidence score, the reasons for each match, evidence snippets, and a suggested order. When `clarification_needed` is true, ask the user the returned `clarification_questions` before you commit to a skill.
- `skillroute.search`: a keyword-style lookup (`query`) when the user wants to browse rather than route one task.
- `skillroute.inspect_skill`: full metadata, relationships, excerpts, and source paths for one `skill_id` from a route or search result.

## When the catalog is empty

A route that returns no candidates usually means nothing has been indexed yet. The catalog lives at `~/.skillroute/catalog.db`. Offer to build it by indexing each skill directory the user cares about. Each run adds to the catalog:

```bash
uvx skillroute index --root ~/.claude/skills
uvx skillroute index --root ./.claude/skills
```

Use `skillroute index` instead of `uvx skillroute index` if the `skillroute` CLI is installed (`pipx install skillroute` or `brew install erichare/skillroute/skillroute`). Re-run the command after skills change.

## Using the results

- Treat the ranking as a shortlist, not a verdict. Read the top candidate's reasons and evidence before loading the skill, and say which skill you picked and why.
- A low top confidence (under about 0.3), or several candidates with close scores, is a signal to ask the user rather than guess.
- Use the local backend unless the user asks otherwise. The `astra` backend sends the query to the user's own Astra DB database and needs `ASTRA_DB_*` credentials in their environment, so choose it only when the user asks for it.
