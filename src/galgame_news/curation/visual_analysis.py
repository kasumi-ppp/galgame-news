"""Optional, local-only OpenCLIP shadow annotations for downloaded images.

These results are deliberately not consumed by image typing, entity matching,
ranking, or selection. Human evaluation is required before any such use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


VISUAL_PROMPTS = {
    "game_cg": "an anime visual novel event CG scene, a finished illustrated background scene",
    "gameplay_screenshot": "a screenshot of a Japanese visual novel game in progress with dialogue UI",
    "character_art": "a character standing portrait or sprite from an anime visual novel",
    "key_visual": "a promotional key visual for a Japanese visual novel game",
    "cover": "the box cover or package art of a Japanese visual novel game",
    "goods": "merchandise or physical goods for an anime game, such as an acrylic stand or tapestry",
    "announcement_art": "an announcement or commemorative illustration for an anime game",
    "photo": "a real-world photograph of a person, event, or physical object",
    "logo": "a game title logo on a plain or transparent background",
    "banner": "a wide web banner, header, or advertising strip",
    "ui": "a small game interface icon, button, or user interface element",
}


class ImageAnalyzer(Protocol):
    def analyze(self, paths: list[Path]) -> list[dict[str, float]]: ...


@dataclass(frozen=True)
class VisualAnalysisResult:
    analyzed: int
    cache_hits: int
    failed: int


def create_local_openclip_analyzer(model_name: str, weights_path: str | Path, device: str = "cpu") -> ImageAnalyzer:
    """Load a local OpenCLIP checkpoint; never resolve/download a model name."""
    checkpoint = Path(weights_path).expanduser()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"local visual model weights not found: {checkpoint}")
    try:
        import open_clip
        import torch
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("visual analysis requires the optional open_clip_torch dependency") from exc

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=str(checkpoint), device=device,
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(model_name)
    prompts = list(VISUAL_PROMPTS.values())
    with torch.no_grad():
        text = tokenizer(prompts).to(device)
        text_features = model.encode_text(text)
        text_features /= text_features.norm(dim=-1, keepdim=True)

    class LocalOpenClipAnalyzer:
        def analyze(self, paths: list[Path]) -> list[dict[str, float]]:
            tensors = []
            for path in paths:
                with Image.open(path) as image:
                    tensors.append(preprocess(image.convert("RGB")))
            batch = torch.stack(tensors).to(device)
            with torch.no_grad():
                image_features = model.encode_image(batch)
                image_features /= image_features.norm(dim=-1, keepdim=True)
                probabilities = (100.0 * image_features @ text_features.T).softmax(dim=-1)
            return [
                {name: float(probabilities[row, col].item()) for col, name in enumerate(VISUAL_PROMPTS)}
                for row in range(len(paths))
            ]

    return LocalOpenClipAnalyzer()


class VisualAnalysisService:
    def __init__(self, analyzer: ImageAnalyzer, *, cache_path: str | Path, batch_size: int = 8):
        self.analyzer = analyzer
        self.cache_path = Path(cache_path)
        self.batch_size = max(1, int(batch_size))

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _read_cache(self) -> dict[str, dict[str, float]]:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_cache(self, cache: dict[str, dict[str, float]]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.cache_path)
        except OSError:
            # The cache is an optimization only; inference results still apply.
            pass

    def annotate(self, candidates) -> VisualAnalysisResult:
        cache = self._read_cache()
        pending: dict[str, list[tuple[object, Path]]] = {}
        hits = analyzed = failed = 0
        for candidate in candidates:
            raw_path = candidate.local_path
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_file():
                continue
            try:
                key = self._digest(path)
            except OSError:
                failed += 1
                continue
            if key in cache:
                self._apply(candidate, cache[key])
                hits += 1
            else:
                pending.setdefault(key, []).append((candidate, path))

        unique = list(pending.items())
        for start in range(0, len(unique), self.batch_size):
            batch = unique[start:start + self.batch_size]
            try:
                outputs = self.analyzer.analyze([items[0][1] for _, items in batch])
                if len(outputs) != len(batch):
                    raise ValueError("visual analyzer returned an unexpected batch length")
            except Exception:
                failed += sum(len(items) for _, items in batch)
                continue
            for (key, items), scores in zip(batch, outputs):
                clean = {str(name): float(score) for name, score in scores.items() if _valid_score(score)}
                if not clean:
                    failed += len(items)
                    continue
                cache[key] = clean
                for candidate, _ in items:
                    self._apply(candidate, clean)
                    analyzed += 1
        if analyzed:
            self._write_cache(cache)
        return VisualAnalysisResult(analyzed=analyzed, cache_hits=hits, failed=failed)

    @staticmethod
    def _apply(candidate, scores: dict[str, float]) -> None:
        label, score = max(scores.items(), key=lambda pair: pair[1])
        candidate.signals["visual_shadow_type"] = label
        candidate.signals["visual_shadow_confidence"] = score
        candidate.signals["visual_shadow_mode"] = "shadow"
        for name, value in scores.items():
            candidate.signals[f"visual_shadow_{name}"] = value


def _valid_score(value: object) -> bool:
    try:
        return 0.0 <= float(value) <= 1.0
    except (TypeError, ValueError):
        return False
