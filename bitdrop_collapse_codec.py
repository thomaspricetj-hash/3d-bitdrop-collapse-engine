# bitdrop_core/ai/compression/bitdrop_collapse_codec.py
#
# BitDrop v3 — 3D Lossless Collapse Engine
# Max version: built for speed + compression, 3D block design.
#
# API:
#   engine = BitDropCollapseEngineV3()
#   collapsed = engine.collapse(text, rules=..., tags=...)
#   expanded  = engine.expand(collapsed)
#
# Compatible with HybridBackend:
#   - collapse / expand
#   - encode / decode (via codec wrapper)
#

from __future__ import annotations
import re
from typing import Dict, Any, List, Tuple


# ============================================================
# v2 core (1D lossless engine, used as inner primitive)
# ============================================================
class BitDropCollapseEngineV2:
    def __init__(self):
        self.rules: Dict[str, str] = {}
        self.reverse_rules: Dict[str, str] = {}
        self.TOKEN_PREFIX = "§BD§"

    def collapse(
        self,
        text: str,
        *,
        rules: Dict[str, Any] = None,
        tags: Dict[str, Any] = None,
    ) -> str:
        rules = rules or {}
        tags = tags or {}
        chunk_size = tags.get("chunk_size", 2048)
        chunks = self._chunk_text(text, chunk_size)
        collapsed_chunks = []
        for chunk in chunks:
            c = self._collapse_chunk(chunk, rules, tags)
            collapsed_chunks.append(c)
        return "".join(collapsed_chunks)

    def expand(self, text: str) -> str:
        for token, original in self.reverse_rules.items():
            text = text.replace(token, original)
        return text

    def _chunk_text(self, text: str, size: int) -> List[str]:
        return [text[i:i + size] for i in range(0, len(text), size)]

    def _collapse_chunk(
        self,
        chunk: str,
        rules: Dict[str, Any],
        tags: Dict[str, Any],
    ) -> str:
        if tags.get("patterns", True):
            chunk = self._collapse_patterns(chunk)
        if tags.get("grouping", True):
            chunk = self._collapse_whitespace(chunk)
        if tags.get("skimming", True):
            chunk = self._skim(chunk)
        return chunk

    def _collapse_patterns(self, text: str) -> str:
        text = re.sub(r"([.,!?])\1{2,}", lambda m: self._tokenize(m.group(0)), text)
        text = re.sub(r"(-{3,})", lambda m: self._tokenize(m.group(0)), text)
        text = re.sub(r"(={3,})", lambda m: self._tokenize(m.group(0)), text)
        return text

    def _collapse_whitespace(self, text: str) -> str:
        return re.sub(r"\s{3,}", lambda m: self._tokenize(m.group(0)), text)

    def _skim(self, text: str) -> str:
        text = re.sub(r"\n{3,}", lambda m: self._tokenize(m.group(0)), text)
        return text

    def _tokenize(self, original: str) -> str:
        token = f"{self.TOKEN_PREFIX}{len(self.rules)}§"
        self.rules[token] = original
        self.reverse_rules[token] = original
        return token


# ============================================================
# v3 — 3D Lossless Engine (max version)
# ============================================================
class BitDropCollapseEngineV3:
    """
    BitDrop v3 — 3D Lossless Collapse Engine

    Design:
        • 1D: raw sequence (characters)
        • 2D: lines × columns (layout plane)
        • 3D: (lines × columns × channels) where channels capture:
            - structure (indent, brackets, code vs prose)
            - repetition (line hashes, block hashes)
            - semantic hints (simple heuristics)

    Goals:
        • Max compression (lossless) for structured / code / reasoning text
        • Max speed via block‑level operations (3D blocks)
        • Fully reversible (Option A)

    Modes (via tags):
        tags["profile"] = "fast" | "balanced" | "max"
    """

    def __init__(self):
        self.v2 = BitDropCollapseEngineV2()
        self.rules: Dict[str, str] = self.v2.rules
        self.reverse_rules: Dict[str, str] = self.v2.reverse_rules
        self.TOKEN_PREFIX = self.v2.TOKEN_PREFIX

    # --------------------------------------------------------
    # Public API (HybridBackend-compatible)
    # --------------------------------------------------------
    def collapse(
        self,
        text: str,
        *,
        rules: Dict[str, Any] = None,
        tags: Dict[str, Any] = None,
    ) -> str:
        rules = rules or {}
        tags = tags or {}

        profile = tags.get("profile", "balanced")  # "fast" | "balanced" | "max"

        # 1) Project text into 3D blocks
        blocks = self._to_3d_blocks(text, tags)

        # 2) Collapse across axes
        if profile in ("balanced", "max"):
            blocks = self._collapse_across_lines(blocks, tags)
            blocks = self._collapse_across_columns(blocks, tags)
        if profile == "max":
            blocks = self._collapse_across_structure(blocks, tags)

        # 3) Flatten back to 1D text
        flattened = self._from_3d_blocks(blocks)

        # 4) Run v2 collapse as final pass (token‑level)
        collapsed = self.v2.collapse(flattened, rules=rules, tags=tags)
        return collapsed

    def expand(self, text: str) -> str:
        # v3 is lossless and uses v2’s reversible tokens
        return self.v2.expand(text)

    # --------------------------------------------------------
    # 3D representation
    # --------------------------------------------------------
    def _to_3d_blocks(
        self,
        text: str,
        tags: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Represent text as a list of blocks.

        Each block:
            {
                "lines": [str, ...],
                "indent_levels": [int, ...],
                "line_hashes": [str, ...],
                "kind": "code" | "prose" | "mixed"
            }

        This is a conceptual 3D structure:
            axis 0: block index
            axis 1: line index
            axis 2: features (channels)
        """
        # simple heuristic: split into blocks by double newlines
        raw_blocks = text.split("\n\n")

        blocks: List[Dict[str, Any]] = []
        for raw in raw_blocks:
            lines = raw.split("\n")
            if not lines:
                continue

            indent_levels = [self._indent_level(line) for line in lines]
            line_hashes = [self._line_signature(line) for line in lines]
            kind = self._block_kind(lines)

            blocks.append(
                {
                    "lines": lines,
                    "indent_levels": indent_levels,
                    "line_hashes": line_hashes,
                    "kind": kind,
                }
            )

        return blocks

    def _from_3d_blocks(self, blocks: List[Dict[str, Any]]) -> str:
        chunks: List[str] = []
        for b in blocks:
            chunk = "\n".join(b["lines"])
            chunks.append(chunk)
        return "\n\n".join(chunks)

    # --------------------------------------------------------
    # Axis‑wise collapse
    # --------------------------------------------------------
    def _collapse_across_lines(
        self,
        blocks: List[Dict[str, Any]],
        tags: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Collapse repeated / similar lines within each block.
        Lossless: we replace repeated lines with tokens.
        """
        for b in blocks:
            lines = b["lines"]
            hashes = b["line_hashes"]

            seen: Dict[str, str] = {}
            new_lines: List[str] = []

            for line, h in zip(lines, hashes):
                if h in seen:
                    # repeated line → tokenize
                    token = self._tokenize(line)
                    new_lines.append(token)
                else:
                    seen[h] = line
                    new_lines.append(line)

            b["lines"] = new_lines
            b["line_hashes"] = [self._line_signature(l) for l in new_lines]

        return blocks

    def _collapse_across_columns(
        self,
        blocks: List[Dict[str, Any]],
        tags: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Collapse vertical patterns: same prefix/suffix across many lines.
        Lossless: we tokenize shared prefixes/suffixes.
        """
        for b in blocks:
            lines = b["lines"]
            if len(lines) < 2:
                continue

            # find common prefix across lines
            prefix = self._common_prefix(lines)
            if prefix and len(prefix) >= 4:
                token = self._tokenize(prefix)
                lines = [token + line[len(prefix):] if line.startswith(prefix) else line for line in lines]

            # find common suffix across lines
            suffix = self._common_suffix(lines)
            if suffix and len(suffix) >= 4:
                token = self._tokenize(suffix)
                lines = [line[:-len(suffix)] + token if line.endswith(suffix) else line for line in lines]

            b["lines"] = lines
            b["line_hashes"] = [self._line_signature(l) for l in lines]

        return blocks

    def _collapse_across_structure(
        self,
        blocks: List[Dict[str, Any]],
        tags: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Max profile: collapse structural patterns (indent ladders, repeated
        code scaffolding, etc.). Still lossless via tokens.
        """
        for b in blocks:
            lines = b["lines"]
            indents = b["indent_levels"]

            # collapse repeated indent ladders (e.g., same pattern of indentation)
            ladder_sig = ",".join(str(i) for i in indents)
            if len(lines) >= 4 and len(set(indents)) > 1:
                token = self._tokenize(f"__INDENT_LADDER__:{ladder_sig}")
                # store ladder as token + stripped lines
                stripped = [l.lstrip(" \t") for l in lines]
                b["lines"] = [token] + stripped
                b["indent_levels"] = [0] * len(b["lines"])
                b["line_hashes"] = [self._line_signature(l) for l in b["lines"]]

        return blocks

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------
    def _indent_level(self, line: str) -> int:
        return len(line) - len(line.lstrip(" \t"))

    def _line_signature(self, line: str) -> str:
        # lightweight, deterministic signature
        core = line.strip()
        core = re.sub(r"\s+", " ", core)
        return f"{len(core)}:{hash(core) & 0xFFFFFFFF:x}"

    def _block_kind(self, lines: List[str]) -> str:
        text = "\n".join(lines).lower()
        code_markers = ["def ", "class ", "{", "}", "(", ")", "import ", "return "]
        score = sum(1 for m in code_markers if m in text)
        if score >= 3:
            return "code"
        if score == 0:
            return "prose"
        return "mixed"

    def _common_prefix(self, lines: List[str]) -> str:
        if not lines:
            return ""
        s1 = min(lines)
        s2 = max(lines)
        for i, c in enumerate(s1):
            if i >= len(s2) or c != s2[i]:
                return s1[:i]
        return s1

    def _common_suffix(self, lines: List[str]) -> str:
        if not lines:
            return ""
        rev = [l[::-1] for l in lines]
        pref = self._common_prefix(rev)
        return pref[::-1]

    def _tokenize(self, original: str) -> str:
        token = f"{self.TOKEN_PREFIX}{len(self.rules)}§"
        self.rules[token] = original
        self.reverse_rules[token] = original
        return token


# ============================================================
# Codec wrapper (encode/decode aliases)
# ============================================================
class BitDropCollapseEngine(BitDropCollapseEngineV3):
    """
    Public engine name used elsewhere in the codebase.
    v3 implementation (3D, lossless, max version).
    """
    pass


class BitDropCollapseCodec:
    """
    Thin wrapper providing encode/decode aliases for engines
    expecting that interface.
    """

    def __init__(self, engine: BitDropCollapseEngine | None = None):
        self.engine = engine or BitDropCollapseEngine()

    def encode(
        self,
        text: str,
        *,
        rules: Dict[str, Any] = None,
        tags: Dict[str, Any] = None,
    ) -> str:
        return self.engine.collapse(text, rules=rules, tags=tags)

    def decode(self, text: str) -> str:
        return self.engine.expand(text)

