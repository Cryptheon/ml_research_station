"""Fixed-vocabulary topic taxonomy.

The ``TOPICS`` list is the canonical set of labels used across the DB, API,
and frontend.  The classifier assigns zero or more labels to a paper using
lightweight keyword heuristics so that topics are consistent without needing
an LLM call during ingestion.
"""

from __future__ import annotations

# ── Fixed vocab ───────────────────────────────────────────────────────────────

TOPICS: list[str] = [
    "LLM",
    "Efficiency",
    "Architecture",
    "Training",
    "Interpretability",
    "RL",
    "World Models",
    "Geometric",
    "Foundations",
    "Representation",
    "Bio",
    "Imaging",
    "Foundation",
    "Generative",
    "Audio",
]

# ── Keyword rules ─────────────────────────────────────────────────────────────

_RULES: list[tuple[str, list[str]]] = [
    (
        "LLM",
        [
            "language model",
            "llm",
            "gpt",
            "transformer",
            "autoregressive",
            "instruction tuning",
            "rlhf",
            "fine-tun",
            "chat",
            "token",
            "next-token",
            "in-context learning",
        ],
    ),
    (
        "Efficiency",
        [
            "efficient",
            "sparsity",
            "sparse",
            "pruning",
            "quantization",
            "distillation",
            "flops",
            "throughput",
            "latency",
            "mixture-of-depths",
            "mixture of depths",
            "mixture-of-experts",
            "moe",
            "early exit",
            "caching",
        ],
    ),
    (
        "Architecture",
        [
            "architecture",
            "attention",
            "transformer",
            "convolution",
            "ssm",
            "state space",
            "mamba",
            "mixture-of-depths",
            "routing",
            "gating",
        ],
    ),
    (
        "Training",
        [
            "training",
            "pretraining",
            "continual",
            "curriculum",
            "data augmentation",
            "optimizer",
            "learning rate",
            "batch size",
            "scaling law",
            "replay",
        ],
    ),
    (
        "Interpretability",
        [
            "interpretab",
            "mechanistic",
            "circuit",
            "probing",
            "feature attribution",
            "saliency",
            "ablation",
            "causal tracing",
            "neuron",
            "sparse autoencode",
        ],
    ),
    (
        "RL",
        [
            "reinforcement learning",
            "reward",
            "policy gradient",
            "ppo",
            "dpo",
            "agent",
            "planning",
            "mcts",
            "q-learning",
            "actor-critic",
        ],
    ),
    (
        "World Models",
        [
            "world model",
            "latent dynamics",
            "model-based",
            "proprioception",
            "dreamer",
            "imagination",
            "environment model",
        ],
    ),
    (
        "Geometric",
        [
            "geometric",
            "equivariant",
            "graph neural",
            "point cloud",
            "manifold",
            "mesh",
            "symmetry",
            "lie group",
            "non-euclidean",
        ],
    ),
    (
        "Foundations",
        [
            "theory",
            "generalization",
            "convergence",
            "statistical learning",
            "pac learning",
            "information theory",
            "survey",
            "unification",
        ],
    ),
    (
        "Representation",
        [
            "representation learning",
            "embedding",
            "contrastive",
            "self-supervised",
            "ssl",
            "byol",
            "simclr",
            "mae",
            "masked autoencoder",
            "dim reduction",
        ],
    ),
    (
        "Bio",
        [
            "protein",
            "genomic",
            "rna",
            "dna",
            "molecular",
            "cell",
            "cryo-em",
            "bioinformatics",
            "evolutionary",
            "sequence model",
            "alphafold",
            "esmfold",
        ],
    ),
    (
        "Imaging",
        [
            "image",
            "segmentation",
            "detection",
            "vision",
            "convnet",
            "denoising",
            "reconstruction",
            "super-resolution",
            "medical imaging",
            "micrograph",
        ],
    ),
    (
        "Foundation",
        [
            "foundation model",
            "multimodal",
            "vision-language",
            "vlm",
            "clip",
            "grounding",
            "zero-shot",
            "few-shot",
        ],
    ),
    (
        "Generative",
        [
            "diffusion",
            "flow matching",
            "gan",
            "vae",
            "generative",
            "score matching",
            "denoising score",
            "discrete flow",
        ],
    ),
    (
        "Audio",
        [
            "audio",
            "speech",
            "music",
            "sound",
            "acoustic",
            "asr",
            "tts",
            "symbolic music",
            "waveform",
        ],
    ),
]


def classify(title: str, abstract: str | None = None) -> list[str]:
    """Return a deduplicated list of topic labels for the given paper text.

    Uses lowercase substring matching against pre-defined keyword lists —
    O(topics × keywords) but fast enough for batch ingestion.
    """
    text = (title + " " + (abstract or "")).lower()
    found: list[str] = []
    for label, keywords in _RULES:
        if any(kw in text for kw in keywords):
            found.append(label)
    return found
