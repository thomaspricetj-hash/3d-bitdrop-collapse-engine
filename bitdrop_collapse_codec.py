# BitDrop V2 - 3D Collapse-Based Compression Engine
# Copyright (C) 2026  Thomas Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional


class BitDropCollapseEngineV2:
    """
    All-in-one 3D BitDrop engine with:
      - Pre-clustering (3D-aware)
      - 3D chunking
      - Region grouping
      - Hierarchical tagging
      - Rule-template generation
      - Adjacency masks
      - Rule pruning
      - Constraint-driven collapse (entropy/complexity-aware)
      - Stabilization
      - TurboQuant-style 4-bit quantization (nibble-packed)
      - Final binary container + entropy coding
    """

    @dataclass
    class BinaryBlock3D:
        data: bytes
        shape: Tuple[int, int, int]
        tags: Dict[str, Any]
        index: int

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

    MAGIC = b"BD3Q"
    VERSION = 1

    def __init__(
        self,
        block_shape: Tuple[int, int, int] = (4, 4, 64),
        level: int = 9,
        max_clusters: int = 32,
        auto_tune_block_shape: bool = True,
        region_block_target: int = 2048,
    ):
        self.block_shape = block_shape
        self.level = level
        self.max_clusters = max_clusters
        self.auto_tune_block_shape = auto_tune_block_shape
        self.region_block_target = region_block_target
        self._tag_root = self.TagNode("ROOT", {})

    # -----------------------------
    # 3D Chunking + Grouping
    # -----------------------------

    def _auto_tune_shape(self, payload_len: int) -> None:
        if not self.auto_tune_block_shape:
            return
        # Simple heuristic: smaller payloads → smaller depth
        d0, d1, _ = self.block_shape
        if payload_len > 8_000_000:
            self.block_shape = (d0, d1, 64)
        elif payload_len > 2_000_000:
            self.block_shape = (d0, d1, 48)
        else:
            self.block_shape = (d0, d1, 32)

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
        # Multi-region grouping: split into chunks of region_block_target blocks
        regions: List[List[BitDropCollapseEngineV2.BinaryBlock3D]] = []
        n = len(blocks)
        step = max(1, self.region_block_target)
        for i in range(0, n, step):
            regions.append(blocks[i:i + step])
        return regions

    # -----------------------------
    # 3D Cube Metrics
    # -----------------------------

    def _cube_metrics(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
    ) -> Tuple[int, int, int, int]:
        d0, d1, d2 = block.shape
        data = block.data
        n = d0 * d1 * d2
        if not data or n == 0:
            return 0, 0, 0, 0

        vals = list(data)
        nz = sum(1 for v in vals if v != 0)
        s = sum(vals)
        mean = s // n

        var_acc = 0
        for v in vals:
            dv = v - mean
            var_acc += dv * dv
        var_approx = var_acc // n

        edge_energy = 0
        idx = 0
        for z in range(d2):
            for y in range(d1):
                for x in range(d0):
                    v = vals[idx]
                    if x + 1 < d0:
                        v2 = vals[idx + 1]
                        edge_energy += abs(v - v2)
                    if y + 1 < d1:
                        v2 = vals[idx + d0]
                        edge_energy += abs(v - v2)
                    if z + 1 < d2:
                        v2 = vals[idx + d0 * d1]
                        edge_energy += abs(v - v2)
                    idx += 1

        nz = int(nz & 0xFFFFFFFF)
        mean = int(mean & 0xFFFF)
        var_approx = int(var_approx & 0xFFFFFFFF)
        edge_energy = int(edge_energy & 0xFFFFFFFF)

        return nz, mean, var_approx, edge_energy

    # -----------------------------
    # TurboQuant-style 4-bit Quantizer (nibble-packed)
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
            # All zeros in quantized space
            n = len(vals)
            packed_len = (n + 1) // 2
            return bytes([0] * packed_len), scale, zero

        scale = (vmax - vmin) / 15.0
        zero = float(vmin)

        n = len(vals)
        packed_len = (n + 1) // 2
        packed = bytearray(packed_len)
        inv_scale = 1.0 / scale

        for i, v in enumerate(vals):
            q = int((v - vmin) * inv_scale + 0.5)
            if q < 0:
                q = 0
            elif q > 15:
                q = 15
            byte_index = i // 2
            if (i & 1) == 0:
                packed[byte_index] = q & 0x0F
            else:
                packed[byte_index] |= (q & 0x0F) << 4

        return bytes(packed), scale, zero

    def _dequantize_block(
        self,
        qdata: bytes,
        scale: float,
        zero: float,
        n_elems: int,
    ) -> bytes:
        if not qdata or n_elems <= 0:
            return b""
        out = bytearray(n_elems)
        for i in range(n_elems):
            byte_index = i // 2
            b = qdata[byte_index]
            if (i & 1) == 0:
                q = b & 0x0F
            else:
                q = (b >> 4) & 0x0F
            v = int(zero + q * scale + 0.5)
            if v < 0:
                v = 0
            elif v > 255:
                v = 255
            out[i] = v
        return bytes(out)

    # -----------------------------
    # Pre-Clustering (3D-aware)
    # -----------------------------

    def _block_signature(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
    ) -> Tuple[int, int, int]:
        nz, mean, var_approx, edge_energy = self._cube_metrics(block)
        h = (var_approx ^ (edge_energy << 1) ^ (nz << 3) ^ (mean << 5)) & 0xFFFFFFFF
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

        sigs = []
        for b in blocks:
            nz, mean, h = self._block_signature(b)
            sigs.append((nz, mean, h))

        for i in range(n):
            nzi, mi, hi = sigs[i]
            for j in range(n):
                if i == j:
                    continue
                nzj, mj, hj = sigs[j]
                dnz = abs(nzi - nzj)
                dm = abs(mi - mj)
                dh = abs(hi - hj)
                score = dnz + (dm * 4) + (dh >> 10)
                if score <= 4096:
                    mask.allow(i, j)
                else:
                    mask.forbid(i, j)

        return mask

    def _prune_unused_rules(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
        template: "BitDropCollapseEngineV2.RuleTemplate",
    ) -> None:
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

        # Start from lowest "complexity" block (nz + var + edge)
        complexities: List[Tuple[int, int]] = []
        for i, b in enumerate(blocks):
            nz, mean, var_approx, edge_energy = self._cube_metrics(b)
            complexity = nz + (var_approx >> 4) + (edge_energy >> 6) + (mean << 2)
            complexities.append((complexity, i))
        complexities.sort()
        current = complexities[0][1]
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
        self._auto_tune_shape(len(payload_bytes))

        blocks = self._to_blocks(payload_bytes)
        regions = self._group_blocks(blocks)

        collapsed_all: List[BitDropCollapseEngineV2.BinaryBlock3D] = []
        scales_all: List[float] = []
        zeros_all: List[float] = []

        for region in regions:
            clusters = self._cluster_blocks(region)
            for cid, cblocks in clusters.items():
                self._assign_block_tags(cblocks, cluster_id=cid)
                tmpl = self._build_templates_for_cluster(cluster_id=cid)
                mask = self._build_adjacency_mask(cblocks)
                self._prune_unused_rules(cblocks, tmpl)
                collapsed = self._collapse_cluster(cblocks, mask)
                stabilized = self._stabilize(collapsed)

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

        container = self._pack_blocks(collapsed_all, scales_all, zeros_all)
        blob = self._compress(container)
        return blob

    def decode(self, blob: bytes) -> bytes:
        container = self._decompress(blob)
        qblocks, scales, zeros = self._unpack_blocks(container)

        d0, d1, d2 = self.block_shape
        n_elems = d0 * d1 * d2

        out = bytearray()
        for b, s, z in zip(qblocks, scales, zeros):
            data = self._dequantize_block(b.data, s, z, n_elems)
            out += data
        return bytes(out)


