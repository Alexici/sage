"""The language models Sage knows how to start (replaces the cases of the old `llmup` fish function).

Sizes are ESTIMATES (marked in the notes) and only drive a "fits on this machine" hint, never a refusal. A model's
sampler settings belong to the model: the client never sends any."""
from dataclasses import dataclass

CONTEXTS = (8192, 16384, 32768)
DEFAULT_MODEL = "gemma12b"
DEFAULT_CTX = 32768
KV_ALLOWANCE_GB = 1.5   # estimate: room for the context cache on top of the weights


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    note: str
    repo: str                    # Hugging Face repo, optionally ":quant", as llama-server's -hf takes it
    sampler: tuple[str, ...]     # llama-server flags, one list item per argument
    vram_gb: float               # estimate: weights on the GPU
    download_gb: float           # estimate
    rank: int                    # lower = preferred when several fit
    listed: bool = True          # False: kept installable but not offered in the setup page


GEMMA = ("--temp", "1.0", "--top-k", "64", "--top-p", "0.95")

REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec("gemma12b", "Gemma 4 12B QAT", "Fast, leaves VRAM free. The default persona model.",
              "unsloth/gemma-4-12b-it-qat-GGUF", GEMMA, 8.0, 8.0, 1),
    ModelSpec("gemma26b", "Gemma 4 26B-A4B", "Quality mode: bigger, slower to load.",
              "bartowski/google_gemma-4-26B-A4B-it-GGUF:Q4_K_M", GEMMA, 17.0, 17.0, 2),
    ModelSpec("mistral24b", "Mistral Small 3.2 24B", "Alternative voice; runs at temperature 0.9.",
              "unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF:Q4_K_M", ("--temp", "0.9"), 14.5, 14.5, 3),
    ModelSpec("ministral14b", "Ministral 3 14B", "Smaller Mistral; runs at temperature 0.9.",
              "unsloth/Ministral-3-14B-Instruct-2512-GGUF:Q4_K_M", ("--temp", "0.9"), 8.5, 8.5, 4, listed=False),
    ModelSpec("qwen27b", "Qwen 27B (uncensored)", "Not a persona model; a coder candidate.",
              "0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF:Q4_K_M",
              ("--temp", "0.7", "--top-k", "20", "--top-p", "0.8", "--min-p", "0.0", "--presence-penalty", "1.5"),
              16.5, 16.5, 5, listed=False),
    ModelSpec("cydonia", "Cydonia 24B", "Roleplay tune; sampler values are community defaults (unverified).",
              "bartowski/TheDrummer_Cydonia-24B-v4.3-GGUF:Q4_K_M", ("--temp", "0.8", "--min-p", "0.05"),
              14.5, 14.5, 6, listed=False),
)
_BY_ID = {m.id: m for m in REGISTRY}


def get(model_id: str) -> ModelSpec | None:
    return _BY_ID.get(model_id)


def ids() -> frozenset[str]:
    return frozenset(_BY_ID)


def fits(spec: ModelSpec, vram_gb: float | None) -> bool | None:
    """None when the graphics memory is unknown."""
    return None if vram_gb is None else spec.vram_gb + KV_ALLOWANCE_GB <= vram_gb


def recommend(vram_gb: float | None) -> str | None:
    """The preferred listed model that fits, or None (unknown memory, or nothing fits)."""
    ok = [m for m in REGISTRY if m.listed and fits(m, vram_gb)]
    return min(ok, key=lambda m: m.rank).id if ok else None


def llama_args(spec: ModelSpec, ctx: int, port: int = 8080) -> list[str]:
    """Arguments for llama-server (after the binary). One list item per argument, never a shell string."""
    return ["-hf", spec.repo, "-ngl", "99", "--jinja", "-np", "1", "--no-mmproj", "-c", str(int(ctx)),
            "--port", str(int(port)), *spec.sampler]


def options(vram_gb: float | None, installed=frozenset()) -> list[dict]:
    """The `options.models` list. Unlisted models appear only when already installed."""
    rec = recommend(vram_gb)
    return [{"id": m.id, "label": m.label, "note": m.note, "vram_gb": m.vram_gb, "download_gb": m.download_gb,
             "installed": m.id in installed, "recommended": m.id == rec, "fits": fits(m, vram_gb)}
            for m in REGISTRY if m.listed or m.id in installed]
