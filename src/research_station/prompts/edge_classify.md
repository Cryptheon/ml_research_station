---
name: edge_classify
description: Classifies the typed intellectual relationship between two ML research papers based on their titles and abstracts.
variables:
  - title_a: Title of paper A
  - abstract_a: Abstract of paper A (truncated)
  - title_b: Title of paper B
  - abstract_b: Abstract of paper B (truncated)
---
You are classifying the intellectual relationship between two ML research papers.

Paper A: $title_a
Abstract A: $abstract_a

Paper B: $title_b
Abstract B: $abstract_b

Classify the relationship FROM B's perspective (how does B relate to A?) using EXACTLY ONE type:

  extends    — B directly builds on A's methods, architecture, or theoretical results
  supersedes — B replaces A for the same problem and is demonstrably better in all key dimensions
  challenges — B contradicts, disproves, or provides strong counter-evidence to A's core claims
  applies    — B takes A's technique and applies it to a new domain, task, or modality
  uses       — B employs A as a component, sub-system, or dependency without substantially extending it
  surveys    — B is a review or survey that synthesises A alongside other works
  baseline   — A is used as a comparison benchmark or starting point in B's evaluation
  concurrent — A and B address the same problem independently with no clear precedence
  unrelated  — no meaningful intellectual connection despite surface-level similarity

Then write ONE sentence (max 20 words) describing the specific connection.
Then give a confidence score (0.0–1.0) reflecting how certain you are of the classification.

Respond in this exact format (three lines, no other text):
TYPE: <one word from the list above>
LINK: <one sentence describing the specific connection>
CONF: <float between 0.0 and 1.0>
