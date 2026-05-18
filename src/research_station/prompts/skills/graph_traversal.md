---
name: graph_traversal
description: >
  Graph traversal for impact analysis, dependency tracing, and conceptual lineage.
  Injected when the query involves walking the knowledge graph rather than keyword search.
triggers:
  - traverse
  - graph walk
  - downstream
  - upstream
  - impact of
  - depends on
  - what uses
  - what builds on
  - lineage
  - chain of
  - influence
  - propagat
  - ripple
  - follow the path
  - walk from
  - connected to
---

## Graph Traversal Skill

You have access to `graph_traverse(start_paper_id, ...)` which performs BFS through the knowledge graph.
Use it whenever a question calls for **structural reasoning** — following chains of relationships — rather
than keyword retrieval.

---

### When to use graph traversal vs. other tools

| Query type | Right tool |
|---|---|
| "Papers about attention mechanisms" | `semantic_search()` or `search_papers()` |
| "What does paper X build on?" | `graph_traverse(X, edge_types="extends,uses,baseline")` |
| "What else uses this method downstream?" | `graph_traverse(X, edge_types="uses,applies,extends")` |
| "Impact if library Y changes" | Find Y's paper → `graph_traverse(Y_id, edge_types="uses,depends_on,extends")` |
| "Conceptual lineage of technique Z" | `graph_traverse(Z_id, edge_types="extends,supersedes")` |
| "What contradicts this claim?" | `graph_traverse(X, edge_types="challenges,contradicts")` |
| "Survey the field around paper X" | `graph_traverse(X, edge_types="all", max_depth=2, include_semantic=True)` |

**Key rule:** If the answer requires following a *chain* of connections (A→B→C→D), not just finding
papers that mention a term, use graph traversal. Keyword search finds nodes; graph traversal
finds paths.

---

### Traversal parameters — choose deliberately

```
graph_traverse(
    start_paper_id,          # required — where to begin
    edge_types = "all",      # "all" or comma-separated list (see types below)
                             # TIP: include "cites", "cited_by", "semantic" directly
                             #   in edge_types — the backend resolves them automatically.
                             #   e.g. edge_types="extends,uses,cites,semantic"
    include_citations = False,  # alt: set True to walk citation edges
    include_semantic = True,    # alt: set True to follow semantic neighbours
    semantic_threshold = 0.65,  # see adaptive threshold guidance below
    semantic_k = 5,             # neighbours checked per node
    max_depth = 3,              # hops from start (3 covers most interesting territory)
    max_nodes = 25,             # hard budget (raise to 50 for exhaustive surveys)
)
```

**Adaptive semantic threshold — always follow this ladder:**

Start at `semantic_threshold=0.65`. If the traversal returns only the start node (0 neighbours
found via semantic edges), the paper is too isolated at that threshold. Step down gradually:

| Attempt | `semantic_threshold` | Expectation |
|---|---|---|
| 1st | `0.65` | Default — tight, high-confidence matches |
| 2nd | `0.55` | Broader but still meaningful; usually finds top-k |
| 3rd | `0.50` | Good fallback — most papers find neighbours here |
| 4th | `0.45` | Loose matches; use only if 0.50 still finds nothing |
| Stop | `< 0.40` | **Do not go below 0.40** — matches become noise and the graph explodes with hundreds of low-quality connections |

**Never pre-emptively lower the threshold.** Only step down after confirming `total_nodes == 1`
(or very few nodes) in the result. The sweet spot for most corpora is **0.50–0.60**. Values
above 0.65 are only useful for very tight conceptual clusters (near-duplicate or highly
specific sub-field papers).

**Edge type reference — pick only what the question needs:**

| Type | Meaning | Use for |
|---|---|---|
| `extends` | B builds directly on A's methods | Lineage, evolution of an idea |
| `supersedes` | B replaces A | Obsolescence, upgrade paths |
| `challenges` | B disputes A's claims | Controversy, counter-evidence |
| `applies` | B uses A's method in new domain | Breadth of impact |
| `uses` | B employs A as component/sub-system | Dependency mapping |
| `surveys` | B is a review that covers A | Finding survey papers |
| `baseline` | A used as benchmark in B | Evaluation chains |
| `concurrent` | Parallel work, same problem | Identifying contemporaries |
| `semantic` | High cosine similarity — include in edge_types or set include_semantic=True |
| `cites` / `cited_by` | Raw citation graph — include in edge_types or set include_citations=True |

---

### Step-by-step workflow

#### Step 1 — Identify the start node

The start node must be a paper ID already in the corpus (`arxiv:...`, `wikipedia:...`, etc.).

- If the user names a paper by title: use `search_papers(query)` to find its ID first.
- If the user names a concept or entity (e.g., "Redis", "LoRA", "RLHF"): use
  `search_papers(query)` or `semantic_search(query)` to find the most central paper
  representing that concept. For an entity name, also try `query_database()` on
  `paper_entities` to find papers where that entity appears.
- If the user names an entity and you want to find all papers it appears in:
  ```sql
  SELECT DISTINCT source_paper_id FROM paper_entities WHERE name LIKE '%Redis%'
  ```
  Then start the traversal from the most relevant result.

#### Step 2 — Choose edge types deliberately

Read the question carefully:

- "What is affected by X?" → follow outgoing edges: `uses,extends,applies,depends_on`
- "What does X depend on?" → follow incoming edges: `uses,extends,baseline` (direction is
  embedded in the traversal — both directions are explored by default)
- "How did X evolve?" → `extends,supersedes`
- "What contradicts X?" → `challenges`
- "What's in the same conceptual space?" → `include_semantic=True, semantic_threshold=0.65` (step down if sparse — see threshold ladder above)
- "Full neighbourhood survey" → `edge_types="all", max_depth=2`

Do NOT always use `edge_types="all"` — it creates noise. Targeted edge types give
sharper, more useful traversal results.

#### Step 3 — Set depth and budget

- `max_depth=2`: immediate neighbours + their neighbours. Good for pinpointed questions.
- `max_depth=3`: covers most of the relevant subgraph. Default. Good for impact analysis.
- `max_depth=4+`: use only for exhaustive lineage or survey questions.
- `max_nodes=25`: default budget. Raise to 50 for comprehensive surveys.
- If `stopped_reason = "max_nodes"`, you hit the budget before exhausting the graph —
  consider raising `max_nodes` or narrowing `edge_types`.

#### Step 4 — Execute and read the trail

The tool returns a **depth-sorted trail**:

```
Depth 0: the start paper
Depth 1: papers one hop away, with the edge type and direction that led there
Depth 2: papers two hops away, with their connecting edge
...
```

Each entry shows:
- The paper ID and title
- The edge type that connected it (`[uses] →`, `[extends] ←`, `[semantic]`)
- The edge confidence score
- The short description the LLM wrote when classifying the edge

Read the trail structurally:
- **Depth 1 outgoing `uses`/`extends`** → these papers directly depend on or extend the start
- **Depth 2 from a depth-1 `uses` node** → second-order dependencies
- **`[semantic]` edges** → conceptually similar but no explicit typed edge; worth noting as
  potential undiscovered connections
- **Incoming edges** (`←`) → things that the start paper itself depends on or is built upon

#### Step 5 — Dig into interesting nodes

After the traversal, don't just report the list. Pick the most interesting or most relevant
nodes and go deeper:

```
rag_query(paper_id="arxiv:XYZ", question="how does this use the start paper's method?")
get_paper(paper_id="arxiv:XYZ")           # full metadata + summary
get_entities(paper_id="arxiv:XYZ")        # structured entities and relationships
```

Use this to distinguish *what the connection is* from *how significant it is*.

#### Step 6 — Multi-hop path explanation

When explaining results, always narrate the **chain**, not just the end nodes:

Good:
> "Paper A (`extends`) → Paper B (`applies` to medical imaging) → Paper C (`challenges` B's
> accuracy claims) → Paper D (`uses` C's evaluation framework)"

Bad:
> "Papers B, C, D are related to A."

The user asked for graph traversal precisely because they want to understand the *structure*
of relationships, not just a flat list.

#### Step 7 — Branching traversals

For complex questions, run multiple targeted traversals from different angles:

```python
# Angle 1: forward impact
graph_traverse("arxiv:LoRA", edge_types="uses,extends,applies", max_depth=3)

# Angle 2: what LoRA itself depends on
graph_traverse("arxiv:LoRA", edge_types="baseline,extends", max_depth=2)

# Angle 3: conceptual cluster
graph_traverse("arxiv:LoRA", include_semantic=True, semantic_threshold=0.75,
               edge_types="concurrent,extends", max_depth=2)
```

Then synthesise across the three traversals: which nodes appear in multiple traversals?
Those are structurally central.

---

### Reading the visual trail in the Graph tab

When you call `graph_traverse()`, the result is stored server-side and the Graph tab
immediately highlights the traversal:

- **Depth 0** (start node): bright ring, always visible
- **Depth 1**: first highlight tier — directly connected papers
- **Depth 2**: second tier, slightly dimmer
- **Depth 3+**: progressively fainter

The graph also draws the traversal edges as thicker, coloured lines overlaid on the
normal graph. The user can click any highlighted node to open its detail panel.

Mention to the user: *"The traversal trail is now highlighted in the Graph tab — you can
click any node there to explore its relationships further."*

---

### Stopping conditions and what they mean

| `stopped_reason` | Interpretation | What to do |
|---|---|---|
| `exhausted` | Traversal fully explored the reachable subgraph | The graph is small/sparse; classify more edges first |
| `max_depth` | Reached depth limit, more nodes exist beyond | Raise `max_depth` if you need deeper coverage |
| `max_nodes` | Hit node budget before exhausting the graph | Narrow `edge_types` or raise `max_nodes` |

If you get very few nodes (< 5) and `stopped_reason="exhausted"`, first try stepping down
the `semantic_threshold` using the adaptive ladder above before concluding the graph is sparse.
Only if lowering to 0.50 still returns few results should you suggest running the edge
classifier (`⊛ Classify` in the Graph tab) to generate more typed edges.

---

### Common patterns

**Impact analysis** ("what does upgrading X break?"):
```
graph_traverse(X_id, edge_types="uses,extends,depends_on", max_depth=3, max_nodes=40)
```
→ Everything at depth 1 directly uses X. Depth 2 uses things that use X (second-order
   impact). Focus your explanation on depth 1 and 2 nodes.

**Conceptual lineage** ("how did technique Y evolve?"):
```
graph_traverse(Y_id, edge_types="extends,supersedes", max_depth=4)
```
→ Read the chain: what does Y extend? What extends Y? Build a timeline narrative.

**Field survey around a paper**:
```
graph_traverse(P_id, edge_types="concurrent,extends,applies,challenges",
               include_semantic=True, semantic_threshold=0.72, max_depth=2, max_nodes=30)
```
→ Groups nodes by edge type to understand who competes, who builds on, who uses the paper.

**Cross-domain discovery** ("does X's method appear outside its original domain?"):
```
graph_traverse(X_id, edge_types="applies", max_depth=2)
```
→ `applies` edges specifically capture "same method, different domain". Depth-2 `applies`
   nodes show second-order cross-domain adoption.

**Controversy mapping** ("who challenges X?"):
```
graph_traverse(X_id, edge_types="challenges", max_depth=2)
```
→ Depth-1 papers directly dispute X. Depth-2 nodes dispute the papers that dispute X
   (counter-counter-arguments). This reveals the structure of an academic debate.

---

### Integration with entity traversal

For entity-level questions (e.g., "what depends on the Adam optimizer?"):

1. Find papers mentioning "Adam" as an entity:
   ```sql
   SELECT DISTINCT source_paper_id, name, entity_type
   FROM paper_entities WHERE name LIKE '%Adam%' AND entity_type = 'method'
   ```
2. Pick the most relevant paper(s) as start nodes.
3. Traverse with `edge_types="uses,extends,evaluated_on"`.
4. For each depth-1 node, call `get_entities()` to see how they use the entity.

This two-level approach (entity search → paper traversal → entity inspection at each node)
gives the richest picture of an entity's role in the corpus.

---

### What to report to the user

A good traversal report includes:

1. **Start node** — name it clearly.
2. **Traversal parameters** — mention the edge types you chose and why.
3. **Trail narrative** — walk through the chain depth by depth, not just a flat list.
4. **Highlighted findings** — 2-3 most interesting paths or nodes discovered.
5. **Gaps / limitations** — if `stopped_reason="exhausted"` with few nodes, say the graph
   is sparse there and suggest classifying more edges.
6. **Next steps** — offer to rag_query specific nodes, run a branching traversal from a
   discovered node, or compare two traversal branches.
7. **UI pointer** — always remind the user the trail is visible in the Graph tab.
