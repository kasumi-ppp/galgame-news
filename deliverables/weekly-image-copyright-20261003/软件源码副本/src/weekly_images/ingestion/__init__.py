"""Document ingestion and deterministic news analysis."""

from .analyzer import OpenAINewsAnalyzer, RuleBasedNewsAnalyzer, detect_image_need
from .docx_parser import DocxDocumentParser

__all__ = ["DocxDocumentParser", "OpenAINewsAnalyzer", "RuleBasedNewsAnalyzer", "detect_image_need"]
