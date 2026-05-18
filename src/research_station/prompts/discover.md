---
name: discover
description: Finds non-obvious intellectual connections between an anchor ML paper and a set of candidate papers.
variables:
  - anchor_title: Title of the anchor paper
  - anchor_abstract: Abstract of the anchor paper (truncated)
  - candidate_block: Numbered list of candidate papers (title + abstract excerpt)
---
ANCHOR PAPER: $anchor_title
$anchor_abstract

CANDIDATE PAPERS:
$candidate_block

Find the 2-3 most intellectually surprising or non-obvious connections between the anchor paper and any candidates. Focus on unexpected bridges: shared mathematical structure, analogous problems in different domains, contradictory assumptions, complementary methods, or surprising thematic echoes.

For each connection: name the candidate paper (by number and title), then describe the insight in 2-3 sentences. Prefer depth over breadth.
