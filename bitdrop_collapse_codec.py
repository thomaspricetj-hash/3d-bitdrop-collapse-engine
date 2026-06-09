This file is part of the SyntheticMind / BitDrop project.

Copyright (C) 2026 Thomas Price

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.



# ============================================================
# bitdrop_core/ai/compression/bitdrop_3d_binary_pipeline.py
# BitDrop 3D Binary Pipeline + TurboQuant-style 4-bit quantization
# All-in-one engine: BitDropCollapseEngineV2
# ============================================================

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional


class BitDropCollapseEngineV2:
    """
    All-in-one 3D BitDrop engine with:
      - Pre-clustering
      - 3D chunking
      - Grouping into regions
      - Hierarchical tagging
      - Rule-template generation
      - Adjacency masks
      - Rule pruning
      - Constraint-driven collapse
      - Stabilization
      - TurboQuant-style 4-bit quantization
      - Final binary container + entropy coding
    """

    # -----------------------------
    # Internal data structures
    # -----------------------------

    @dataclass
    class BinaryBlock3D:
        data: bytes          # raw or quantized bytes
        shape: Tuple[int, int, int]
        tags: Dict[str, Any]
        index: int           # original index

    @dataclass
    class TagNode:
        name: str
        children: Dict[str, "BitDropCollapseEngineV2.TagNode"]
        parent: Optional["BitDropCollapseEngineV2.TagNode"] = None

        def path(self) -> Tuple[str, ...]:
            node = self
            out = []
            while node is not None:
                out.append(node.name)
                node = node.parent
            return tuple(reversed(out))

    @dataclass
    class RuleTemplate:
        tag_prefix: Tuple[str, ...]
        max_neighbors: int
        allowed_delta: int

    # -----------------------------
    # Constants
    # -----------------------------

    MAGIC = b"BD3Q"
    VERSION = 1

    # -----------------------------
    # Init
    # -----------------------------

    def __init__(
        self,
        block_shape: Tuple[int, int, int] = (4, 4, 64),
        level: int = 9,
        max_clusters: int = 32,
    ):
        self.block_shape = block_shape
        self.level = level
        self.max_clusters = max_clusters

        # Tag hierarchy root
        self._tag_root = self.TagNode("ROOT", {})

    # -----------------------------
    # 3D Chunking + Grouping
    # -----------------------------

    def _to_blocks(self, payload_bytes: bytes) -> List["BitDropCollapseEngineV2.BinaryBlock3D"]:
        d0, d1, d2 = self.block_shape
        block_size = d0 * d1 * d2
        blocks: List[BitDropCollapseEngineV2.BinaryBlock3D] = []

        for i in range(0, len(payload_bytes), block_size):
            chunk = payload_bytes[i:i + block_size]
            if not chunk:
                break
            if len(chunk) < block_size:
                chunk = chunk + b"\x00" * (block_size - len(chunk))
            blocks.append(
                self.BinaryBlock3D(
                    data=chunk,
                    shape=self.block_shape,
                    tags={},
                    index=len(blocks),
                )
            )

        return blocks

    def _group_blocks(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> List[List["BitDropCollapseEngineV2.BinaryBlock3D"]]:
        if not blocks:
            return []
        # Single region for now; can be extended to multiple semantic regions
        return [blocks]

    # -----------------------------
    # TurboQuant-style 4-bit Quantizer
    # -----------------------------

    def _quantize_block(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
    ) -> Tuple[bytes, float, float]:
        data = block.data
        if not data:
            return b"", 0.0, 1.0

        vals = list(data)
        vmin = min(vals)
        vmax = max(vals)
        if vmax == vmin:
            scale = 1.0
            zero = float(vmin)
            q = bytes([0] * len(vals))
            return q, scale, zero

        scale = (vmax - vmin) / 15.0
        zero = float(vmin)

        q_bytes = bytearray(len(vals))
        inv_scale = 1.0 / scale
        for i, v in enumerate(vals):
            q = int((v - vmin) * inv_scale + 0.5)
            if q < 0:
                q = 0
            elif q > 15:
                q = 15
            q_bytes[i] = q

        return bytes(q_bytes), scale, zero

    def _dequantize_block(self, qdata: bytes, scale: float, zero: float) -> bytes:
        if not qdata:
            return b""
        out = bytearray(len(qdata))
        for i, q in enumerate(qdata):
            v = int(zero + q * scale + 0.5)
            if v < 0:
                v = 0
            elif v > 255:
                v = 255
            out[i] = v
        return bytes(out)

    # -----------------------------
    # Pre-Clustering
    # -----------------------------

    def _block_signature(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
    ) -> Tuple[int, int, int]:
        data = block.data
        nz = sum(1 for b in data if b != 0)
        mean = sum(data) // max(1, len(data))
        h = hash(data) & 0xFFFFFFFF
        return nz, mean, h

    def _cluster_blocks(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> Dict[int, List["BitDropCollapseEngineV2.BinaryBlock3D"]]:
        clusters: Dict[int, List[BitDropCollapseEngineV2.BinaryBlock3D]] = {}
        for b in blocks:
            nz, mean, h = self._block_signature(b)
            bucket = ((nz // 128) ^ (mean // 8) ^ (h >> 8)) % self.max_clusters
            clusters.setdefault(bucket, []).append(b)
        return clusters

    # -----------------------------
    # Hierarchical Tagging
    # -----------------------------

    def _add_tag_path(self, path: List[str]) -> "BitDropCollapseEngineV2.TagNode":
        node = self._tag_root
        for name in path:
            if name not in node.children:
                child = self.TagNode(name=name, children={}, parent=node)
                node.children[name] = child
            node = node.children[name]
        return node

    def _assign_block_tags(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
        cluster_id: int,
    ) -> None:
        for idx, b in enumerate(blocks):
            node = self._add_tag_path(["CLUSTER", str(cluster_id), "BLOCK_GROUP"])
            b.tags["tag_node"] = node
            b.tags["cluster_id"] = cluster_id
            b.tags["local_index"] = idx

    # -----------------------------
    # Rule Templates + Adjacency Masks
    # -----------------------------

    class _AdjacencyMask:
        def __init__(self, n: int):
            self.n = n
            self.mask = bytearray(n * n)

        def _idx(self, i: int, j: int) -> int:
            return i * self.n + j

        def allow(self, i: int, j: int):
            self.mask[self._idx(i, j)] = 1

        def forbid(self, i: int, j: int):
            self.mask[self._idx(i, j)] = 0

        def is_allowed(self, i: int, j: int) -> bool:
            if i == j:
                return False
            return self.mask[self._idx(i, j)] == 1

    def _build_templates_for_cluster(
        self,
        cluster_id: int,
    ) -> "BitDropCollapseEngineV2.RuleTemplate":
        prefix = ("CLUSTER", str(cluster_id))
        tmpl = self.RuleTemplate(
            tag_prefix=prefix,
            max_neighbors=8,
            allowed_delta=1024,
        )
        return tmpl

    def _build_adjacency_mask(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> "_AdjacencyMask":
        n = len(blocks)
        mask = self._AdjacencyMask(n)
        sigs = [hash(b.data) & 0xFFFF for b in blocks]

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if abs(sigs[i] - sigs[j]) <= 1024:
                    mask.allow(i, j)
                else:
                    mask.forbid(i, j)

        return mask

    def _prune_unused_rules(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
        template: "BitDropCollapseEngineV2.RuleTemplate",
    ) -> None:
        # Placeholder: keep all templates for now.
        return

    # -----------------------------
    # Constraint-Driven Collapse + Stabilization
    # -----------------------------

    def _collapse_cluster(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
        mask: "_AdjacencyMask",
    ) -> List["BitDropCollapseEngineV2.BinaryBlock3D"]:
        if not blocks:
            return []

        n = len(blocks)
        used = [False] * n
        order: List[int] = []

        hashes = [(hash(b.data) & 0xFFFFFFFF, i) for i, b in enumerate(blocks)]
        hashes.sort()
        current = hashes[0][1]
        order.append(current)
        used[current] = True

        while len(order) < n:
            best = None
            for j in range(n):
                if used[j]:
                    continue
                if mask.is_allowed(current, j):
                    best = j
                    break
            if best is None:
                for j in range(n):
                    if not used[j]:
                        best = j
                        break
            order.append(best)
            used[best] = True
            current = best

        return [blocks[i] for i in order]

    def _stabilize(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> List["BitDropCollapseEngineV2.BinaryBlock3D"]:
        # Hook for future consistency checks; identity for now.
        return blocks

    # -----------------------------
    # Final Compression Container
    # -----------------------------

    def _pack_blocks(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
        scales: List[float],
        zeros: List[float],
    ) -> bytes:
        if not blocks:
            return (
                self.MAGIC
                + bytes([self.VERSION])
                + struct.pack(">I", 0)
                + struct.pack(">I", 0)
            )

        block_size = len(blocks[0].data)
        n_blocks = len(blocks)

        header = bytearray()
        header += self.MAGIC
        header += bytes([self.VERSION])
        header += struct.pack(">I", n_blocks)
        header += struct.pack(">I", block_size)

        meta = bytearray()
        for s, z in zip(scales, zeros):
            meta += struct.pack(">f", s)
            meta += struct.pack(">f", z)

        body = bytearray()
        for b in blocks:
            body += b.data

        return bytes(header) + bytes(meta) + bytes(body)

    def _compress(self, container: bytes) -> bytes:
        return zlib.compress(container, level=self.level)

    def _decompress(self, blob: bytes) -> bytes:
        return zlib.decompress(blob)

    def _unpack_blocks(
        self,
        container: bytes,
    ) -> Tuple[List["BitDropCollapseEngineV2.BinaryBlock3D"], List[float], List[float]]:
        off = 0
        magic = container[off:off + 4]
        off += 4
        if magic != self.MAGIC:
            raise ValueError("Bad magic")
        ver = container[off]
        off += 1
        if ver != self.VERSION:
            raise ValueError("Bad version")
        n_blocks = struct.unpack(">I", container[off:off + 4])[0]
        off += 4
        block_size = struct.unpack(">I", container[off:off + 4])[0]
        off += 4

        scales: List[float] = []
        zeros: List[float] = []
        for _ in range(n_blocks):
            s = struct.unpack(">f", container[off:off + 4])[0]
            off += 4
            z = struct.unpack(">f", container[off:off + 4])[0]
            off += 4
            scales.append(s)
            zeros.append(z)

        blocks: List[BitDropCollapseEngineV2.BinaryBlock3D] = []
        for i in range(n_blocks):
            chunk = container[off:off + block_size]
            off += block_size
            blocks.append(
                self.BinaryBlock3D(
                    data=chunk,
                    shape=self.block_shape,
                    tags={},
                    index=i,
                )
            )

        return blocks, scales, zeros

    # -----------------------------
    # Public API
    # -----------------------------

    def encode(self, payload_bytes: bytes) -> bytes:
        # 1) Chunk into 3D blocks
        blocks = self._to_blocks(payload_bytes)

        # 2) Group into regions
        regions = self._group_blocks(blocks)

        collapsed_all: List[BitDropCollapseEngineV2.BinaryBlock3D] = []
        scales_all: List[float] = []
        zeros_all: List[float] = []

        # 3) For each region: pre-cluster, tag, rules, collapse, stabilize
        for region in regions:
            clusters = self._cluster_blocks(region)
            for cid, cblocks in clusters.items():
                self._assign_block_tags(cblocks, cluster_id=cid)
                tmpl = self._build_templates_for_cluster(cluster_id=cid)
                mask = self._build_adjacency_mask(cblocks)
                self._prune_unused_rules(cblocks, tmpl)
                collapsed = self._collapse_cluster(cblocks, mask)
                stabilized = self._stabilize(collapsed)

                # 4) TurboQuant-style quantization per block
                for b in stabilized:
                    qdata, scale, zero = self._quantize_block(b)
                    collapsed_all.append(
                        self.BinaryBlock3D(
                            data=qdata,
                            shape=b.shape,
                            tags=b.tags,
                            index=b.index,
                        )
                    )
                    scales_all.append(scale)
                    zeros_all.append(zero)

        # 5) Final container + entropy coding
        container = self._pack_blocks(collapsed_all, scales_all, zeros_all)
        blob = self._compress(container)
        return blob

    def decode(self, blob: bytes) -> bytes:
        # 1) Decompress
        container = self._decompress(blob)
        # 2) Unpack quantized blocks + params
        qblocks, scales, zeros = self._unpack_blocks(container)
        # 3) Dequantize and reassemble
        out = bytearray()
        for b, s, z in zip(qblocks, scales, zeros):
            data = self._dequantize_block(b.data, s, z)
            out += data
        return bytes(out)














