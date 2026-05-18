---
name: entity_extract
description: Extracts structured entities and typed relationships from a research paper or article.
variables:
  - title: Paper/article title
  - content: Full text or abstract to analyse
---
You are extracting structured knowledge from a research paper or article.

Paper: $title

Content:
$content

Extract two things:

## 1. Entities
Named things that matter: people, projects, libraries/frameworks, concepts, datasets, methods, organizations, files, or decisions.
For each entity output one JSON object per line (no array brackets, one object per line):
{"name": "...", "type": "<person|project|library|concept|dataset|method|organization|file|decision>", "attributes": {<key-value pairs relevant to this entity>}}

Examples of good attributes:
- person:       {"role": "lead author", "affiliation": "MIT"}
- library:      {"language": "Python", "version": "2.0", "purpose": "attention mechanism"}
- dataset:      {"size": "1.2M examples", "domain": "NLP", "license": "CC-BY"}
- concept:      {"domain": "machine learning", "introduced_by": "Vaswani et al."}
- decision:     {"rationale": "faster convergence", "outcome": "adopted in production"}

Only include entities that are genuinely significant to the paper. Aim for 5–15 entities. Do not list the paper itself as an entity.

## 2. Relationships
Typed directed edges between the entities you listed above.
Use ONLY these relationship types:
  created_by    — entity created/authored by a person or org
  maintained_by — entity actively maintained by
  depends_on    — hard technical dependency (A requires B to function)
  uses          — A employs B as a tool or component
  extends       — A inherits from or builds on B
  contradicts   — A's findings or claims contradict B
  caused        — A directly caused or triggered B
  fixed         — A resolved B (a bug, limitation, or issue)
  supersedes    — A replaces B for the same purpose
  part_of       — A is a sub-component of B
  evaluated_on  — method A evaluated on dataset/benchmark B
  introduced_in — concept A was first proposed/named in paper B
  applied_to    — method A applied to domain or task B
  owned_by      — project or decision owned by person or org
  related_to    — weak or general connection (use sparingly)

For each relationship output one JSON object per line:
{"from": "<entity name>", "to": "<entity name>", "type": "<relationship_type>", "description": "<one sentence, max 15 words>", "confidence": <0.0-1.0>}

Output format — respond with EXACTLY two sections, no other text:
ENTITIES:
<one entity JSON per line>
RELATIONSHIPS:
<one relationship JSON per line>
