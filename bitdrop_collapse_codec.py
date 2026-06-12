from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional


class BitDropCollapseEngineV2:
    """
    3D BitDrop V2 – tuned for higher pattern exposure with low overhead

      - Global semantic transforms (auto-selected mode):
          * mode 0: none
          * mode 1: vector-wise delta
          * mode 2: dimension-variance permutation + vector-wise delta

      - 3D chunking + region grouping
      - Per-block adaptive transform modes:
          * mode 0: raw
          * mode 1: delta

      - Pre-clustering + adjacency masks
      - Constraint-driven collapse
      - TurboQuant-style 4-bit quantization (cluster range)
      - 4D pair metrics for final ordering
      - zlib container
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
    VERSION = 8  # semantic-mode auto + per-block delta

    def __init__(
        self,
        block_shape: Tuple[int, int, int] = (4, 4, 64),
        level: int = 9,
        max_clusters: int = 32,
        auto_tune_block_shape: bool = True,
        region_block_target: int = 2048,
        use_4d_pairs: bool = True,
        vector_stride: Optional[int] = 128,  # TurboVec-style stride
    ):
        self.block_shape = block_shape
        self.level = level
        self.max_clusters = max_clusters
        self.auto_tune_block_shape = auto_tune_block_shape
        self.region_block_target = region_block_target
        self.use_4d_pairs = use_4d_pairs
        self.vector_stride = vector_stride
        self._tag_root = self.TagNode("ROOT", {})

    # -----------------------------
    # Semantic transforms (global, reversible)
    # -----------------------------

    def _build_dim_permutation(self, data: bytes) -> List[int]:
        if not data or not self.vector_stride or self.vector_stride <= 0:
            return []
        stride = self.vector_stride
        if len(data) < 2 * stride:
            return list(range(stride))

        counts = [0] * stride
        sums = [0.0] * stride
        sums_sq = [0.0] * stride

        n = len(data)
        for i in range(0, n - stride + 1, stride):
            row = data[i:i + stride]
            for j, v in enumerate(row):
                counts[j] += 1
                fv = float(v)
                sums[j] += fv
                sums_sq[j] += fv * fv

        variances: List[Tuple[float, int]] = []
        for j in range(stride):
            c = counts[j]
            if c <= 0:
                variances.append((0.0, j))
            else:
                mean = sums[j] / c
                var = (sums_sq[j] / c) - (mean * mean)
                if var < 0.0:
                    var = 0.0
                variances.append((var, j))

        variances.sort(key=lambda t: t[0])
        perm = [idx for _, idx in variances]
        return perm

    def _apply_dim_permutation(self, data: bytes, perm: List[int]) -> bytes:
        if not data or not perm or not self.vector_stride or self.vector_stride <= 0:
            return data
        stride = self.vector_stride
        n = len(data)
        out = bytearray(n)
        plen = len(perm)
        for base in range(0, n, stride):
            end = min(base + stride, n)
            if end - base < plen:
                out[base:end] = data[base:end]
                continue
            for new_pos, old_pos in enumerate(perm):
                out[base + new_pos] = data[base + old_pos]
        return bytes(out)

    def _apply_dim_inverse_permutation(self, data: bytes, perm: List[int]) -> bytes:
        if not data or not perm or not self.vector_stride or self.vector_stride <= 0:
            return data
        stride = self.vector_stride
        n = len(data)
        out = bytearray(n)
        plen = len(perm)
        inv = [0] * plen
        for new_pos, old_pos in enumerate(perm):
            inv[old_pos] = new_pos
        for base in range(0, n, stride):
            end = min(base + stride, n)
            if end - base < plen:
                out[base:end] = data[base:end]
                continue
            for old_pos, new_pos in enumerate(inv):
                out[base + old_pos] = data[base + new_pos]
        return bytes(out)

    def _forward_vector_delta(self, data: bytes) -> bytes:
        if not data or not self.vector_stride or self.vector_stride <= 0:
            return data
        stride = self.vector_stride
        n = len(data)
        out = bytearray(n)
        for base in range(0, n, stride):
            end = min(base + stride, n)
            prev = 0
            for i in range(base, end):
                v = data[i]
                d = (v - prev) & 0xFF
                out[i] = d
                prev = v
        return bytes(out)

    def _inverse_vector_delta(self, data: bytes) -> bytes:
        if not data or not self.vector_stride or self.vector_stride <= 0:
            return data
        stride = self.vector_stride
        n = len(data)
        out = bytearray(n)
        for base in range(0, n, stride):
            end = min(base + stride, n)
            prev = 0
            for i in range(base, end):
                d = data[i]
                v = (d + prev) & 0xFF
                out[i] = v
                prev = v
        return bytes(out)

    # -----------------------------
    # Heuristic scoring (for zlib-friendliness)
    # -----------------------------

    def _score_bytes_for_zlib(self, data: bytes) -> int:
        if not data:
            return 0
        transitions = 0
        zeros = 0
        prev = data[0]
        if prev == 0:
            zeros += 1
        for v in data[1:]:
            if v != prev:
                transitions += 1
            if v == 0:
                zeros += 1
            prev = v
        return transitions - zeros  # lower is better

    # -----------------------------
    # Semantic mode auto-selection
    # -----------------------------

    def _semantic_forward_auto(self, data: bytes) -> Tuple[bytes, List[int], int]:
        """
        Returns (transformed_data, perm, semantic_mode)
        semantic_mode:
          0 = none
          1 = vector-delta only
          2 = dim-perm + vector-delta
        """
        if not data or not self.vector_stride or self.vector_stride <= 0:
            return data, [], 0

        # sample prefix for scoring
        sample_len = min(len(data), 256 * (self.vector_stride or 1))
        sample = data[:sample_len]

        # mode 0: none
        best_mode = 0
        best_perm: List[int] = []
        best_score = self._score_bytes_for_zlib(sample)

        # mode 1: delta only
        delta_sample = self._forward_vector_delta(sample)
        score_delta = self._score_bytes_for_zlib(delta_sample)
        if score_delta < best_score:
            best_score = score_delta
            best_mode = 1
            best_perm = []

        # mode 2: perm + delta
        perm = self._build_dim_permutation(sample)
        if perm:
            perm_sample = self._apply_dim_permutation(sample, perm)
            perm_delta_sample = self._forward_vector_delta(perm_sample)
            score_perm_delta = self._score_bytes_for_zlib(perm_delta_sample)
            if score_perm_delta < best_score:
                best_score = score_perm_delta
                best_mode = 2
                best_perm = perm

        # apply chosen mode to full data
        if best_mode == 0:
            return data, [], 0
        elif best_mode == 1:
            full = self._forward_vector_delta(data)
            return full, [], 1
        else:
            # mode 2
            if not best_perm:
                best_perm = self._build_dim_permutation(data)
            if best_perm:
                data = self._apply_dim_permutation(data, best_perm)
            full = self._forward_vector_delta(data)
            return full, best_perm, 2

    def _semantic_inverse_with_mode(self, data: bytes, perm: List[int], mode: int) -> bytes:
        if not data:
            return data
        if mode == 0:
            return data
        if mode == 1:
            return self._inverse_vector_delta(data)
        if mode == 2:
            data = self._inverse_vector_delta(data)
            if perm:
                data = self._apply_dim_inverse_permutation(data, perm)
            return data
        return data

    # -----------------------------
    # Per-block transform modes (raw / delta)
    # -----------------------------

    def _forward_delta(self, data: bytes) -> bytes:
        if not data:
            return data
        out = bytearray(len(data))
        prev = 0
        for i, v in enumerate(data):
            d = (v - prev) & 0xFF
            out[i] = d
            prev = v
        return bytes(out)

    def _inverse_delta(self, data: bytes) -> bytes:
        if not data:
            return data
        out = bytearray(len(data))
        prev = 0
        for i, d in enumerate(data):
            v = (d + prev) & 0xFF
            out[i] = v
            prev = v
        return bytes(out)

    def _choose_mode_for_block(self, data: bytes) -> int:
        # try raw vs delta, pick better heuristic score
        raw_score = self._score_bytes_for_zlib(data)
        delta_data = self._forward_delta(data)
        delta_score = self._score_bytes_for_zlib(delta_data)
        return 1 if delta_score < raw_score else 0

    def _apply_mode_forward(self, data: bytes, mode: int) -> bytes:
        if mode == 1:
            return self._forward_delta(data)
        return data

    def _apply_mode_inverse(self, data: bytes, mode: int) -> bytes:
        if mode == 1:
            return self._inverse_delta(data)
        return data

    # -----------------------------
    # 3D Chunking + Grouping
    # -----------------------------

    def _auto_tune_shape(self, payload_len: int) -> None:
        if not self.auto_tune_block_shape:
            return
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

        scored: List[Tuple[Tuple[int, int, int, int], BitDropCollapseEngineV2.BinaryBlock3D]] = []
        for b in blocks:
            nz, mean, var_approx, edge_energy = self._cube_metrics(b)
            mean_band = (mean // 8) & 0x1F
            nz_band = (nz // 256) & 0x3F
            var_band = (var_approx >> 10) & 0x3F
            edge_band = (edge_energy >> 12) & 0x3F
            key = (mean_band, nz_band, var_band, edge_band)
            scored.append((key, b))

        scored.sort(key=lambda t: t[0])
        sorted_blocks = [b for _, b in scored]

        regions: List[List[BitDropCollapseEngineV2.BinaryBlock3D]] = []
        n = len(sorted_blocks)
        step = max(1, self.region_block_target)
        for i in range(0, n, step):
            region = sorted_blocks[i:i + step]
            rid = len(regions)
            for rb in region:
                rb.tags["region_id"] = rid
            regions.append(region)
        return regions

    # -----------------------------
    # 3D / Flat Cube Metrics
    # -----------------------------

    def _cube_metrics(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
    ) -> Tuple[int, int, int, int]:
        d0, d1, d2 = block.shape
        data = block.data
        if not data:
            return 0, 0, 0, 0

        vals = list(data)
        n = len(vals)

        expected_n = d0 * d1 * d2
        if n != expected_n:
            nz = sum(1 for v in vals if v != 0)
            s = sum(vals)
            mean = s // n if n > 0 else 0

            var_acc = 0
            for v in vals:
                dv = v - mean
                var_acc += dv * dv
            var_approx = var_acc // n if n > 0 else 0

            edge_energy = 0

            nz = int(nz & 0xFFFFFFFF)
            mean = int(mean & 0xFFFF)
            var_approx = int(var_approx & 0xFFFFFFFF)
            edge_energy = int(edge_energy & 0xFFFFFFFF)
            return nz, mean, var_approx, edge_energy

        nz = sum(1 for v in vals if v != 0)
        s = sum(vals)
        mean = s // expected_n

        var_acc = 0
        for v in vals:
            dv = v - mean
            var_acc += dv * dv
        var_approx = var_acc // expected_n

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
    # 4D Pair Metrics (ordering only)
    # -----------------------------

    def _pair_metrics_map(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> Dict[int, Tuple[int, int, int, int, int, int, int, int]]:
        if not blocks or not self.use_4d_pairs:
            return {}

        sorted_blocks = sorted(blocks, key=lambda b: b.index)
        pair_map: Dict[int, Tuple[int, int, int, int, int, int, int, int]] = {}

        for i in range(0, len(sorted_blocks), 2):
            b1 = sorted_blocks[i]
            nzA, meanA, varA, edgeA = self._cube_metrics(b1)
            rA = b1.tags.get("region_id", 0)
            cA = b1.tags.get("cluster_id", 0)

            if i + 1 < len(sorted_blocks):
                b2 = sorted_blocks[i + 1]
                nzB, meanB, varB, edgeB = self._cube_metrics(b2)
                rB = b2.tags.get("region_id", 0)
                cB = b2.tags.get("cluster_id", 0)
            else:
                b2 = None
                nzB, meanB, varB, edgeB = nzA, meanA, varA, edgeA
                rB, cB = rA, cA

            nz_sum = nzA + nzB
            mean_avg = (meanA + meanB) // 2
            var_avg = (varA + varB) // 2

            layer_bonus = 0
            if rA == rB:
                layer_bonus += 1
            if cA == cB:
                layer_bonus += 2

            edge_avg = ((edgeA + edgeB) // 2) ^ (layer_bonus << 4)

            nz_delta = abs(nzA - nzB)
            mean_delta = abs(meanA - meanB)
            var_delta = abs(varA - varB)
            edge_delta = abs(edgeA - edgeB)

            sig = (
                nz_sum,
                mean_avg,
                var_avg,
                edge_avg,
                nz_delta,
                mean_delta,
                var_delta,
                edge_delta,
            )

            pair_map[b1.index] = sig
            if b2 is not None:
                pair_map[b2.index] = sig

        return pair_map

    def _pair_signature_for_block(
        self,
        b: "BitDropCollapseEngineV2.BinaryBlock3D",
        pair_map: Dict[int, Tuple[int, int, int, int, int, int, int, int]],
    ) -> int:
        sig = pair_map.get(b.index)
        if sig is None:
            return 0
        nz_sum, mean_avg, var_avg, edge_avg, nz_delta, mean_delta, var_delta, edge_delta = sig
        key = (
            ((nz_sum & 0xFFFF) << 48)
            | ((mean_avg & 0xFFFF) << 32)
            | ((var_avg & 0xFFFF) << 16)
            | (edge_avg & 0xFFFF)
        )
        key ^= ((nz_delta & 0xFF) << 40)
        key ^= ((mean_delta & 0xFF) << 24)
        key ^= ((var_delta & 0xFF) << 8)
        key ^= (edge_delta & 0xFF)
        return key

    # -----------------------------
    # TurboQuant-style 4-bit Quantizer
    # -----------------------------

    def _quantize_block_with_range(
        self,
        block: "BitDropCollapseEngineV2.BinaryBlock3D",
        vmin: int,
        vmax: int,
    ) -> Tuple[bytes, float, float]:
        data = block.data
        if not data:
            return b"", 0.0, 1.0

        if vmax <= vmin:
            scale = 1.0
            zero = float(vmin)
            n = len(data)
            packed_len = (n + 1) // 2
            return bytes([0] * packed_len), scale, zero

        vals = list(data)
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
        return self._quantize_block_with_range(block, vmin, vmax)

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
    # Pre-Clustering
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
        if not blocks:
            return clusters

        for b in blocks:
            nz, mean, h = self._block_signature(b)
            bucket = ((nz // 128) ^ (mean // 8) ^ (h >> 8)) % self.max_clusters
            clusters.setdefault(bucket, []).append(b)

        return clusters

    # -----------------------------
    # Cluster-level quantization helpers
    # -----------------------------

    def _cluster_value_range(
        self,
        blocks: List["BitDropCollapseEngineV2.BinaryBlock3D"],
    ) -> Tuple[int, int]:
        if not blocks:
            return 0, 0

        vmin = 255
        vmax = 0
        for b in blocks:
            if not b.data:
                continue
            vals = b.data
            local_min = min(vals)
            local_max = max(vals)
            if local_min < vmin:
                vmin = local_min
            if local_max > vmax:
                vmax = local_max

        if vmin > vmax:
            vmin = 0
            vmax = 0
        return vmin, vmax

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
        perm: List[int],
        modes: List[int],
        semantic_mode: int,
    ) -> bytes:
        if not blocks:
            header = (
                self.MAGIC
                + bytes([self.VERSION])
                + bytes([semantic_mode & 0xFF])
                + struct.pack(">I", 0)  # n_blocks
                + struct.pack(">I", 0)  # block_size
                + struct.pack(">H", 0)  # perm_len
                + struct.pack(">I", 0)  # modes_len
            )
            return header

        block_size = len(blocks[0].data)
        n_blocks = len(blocks)

        header = bytearray()
        header += self.MAGIC
        header += bytes([self.VERSION])
        header += bytes([semantic_mode & 0xFF])
        header += struct.pack(">I", n_blocks)
        header += struct.pack(">I", block_size)

        perm_bytes = b""
        if perm:
            plen = min(len(perm), 65535)
            header += struct.pack(">H", plen)
            perm_bytes = bytes(perm[:plen])
        else:
            header += struct.pack(">H", 0)

        modes_bytes = b""
        if modes:
            mlen = len(modes)
            header += struct.pack(">I", mlen)
            modes_bytes = bytes(modes)
        else:
            header += struct.pack(">I", 0)

        meta = bytearray()
        for s, z in zip(scales, zeros):
            meta += struct.pack(">f", s)
            meta += struct.pack(">f", z)

        body = bytearray()
        for b in blocks:
            body += b.data

        return bytes(header) + perm_bytes + modes_bytes + bytes(meta) + bytes(body)

    def _compress(self, container: bytes) -> bytes:
        return zlib.compress(container, level=self.level)

    def _decompress(self, blob: bytes) -> bytes:
        return zlib.decompress(blob)

    def _unpack_blocks(
        self,
        container: bytes,
    ) -> Tuple[List["BitDropCollapseEngineV2.BinaryBlock3D"], List[float], List[float], List[int], List[int], int]:
        off = 0
        magic = container[off:off + 4]
        off += 4
        if magic != self.MAGIC:
            raise ValueError("Bad magic")
        ver = container[off]
        off += 1
        if ver != self.VERSION:
            raise ValueError("Bad version")
        semantic_mode = container[off]
        off += 1
        n_blocks = struct.unpack(">I", container[off:off + 4])[0]
        off += 4
        block_size = struct.unpack(">I", container[off:off + 4])[0]
        off += 4
        perm_len = struct.unpack(">H", container[off:off + 2])[0]
        off += 2

        perm: List[int] = []
        if perm_len > 0:
            perm_bytes = container[off:off + perm_len]
            off += perm_len
            perm = [int(b) for b in perm_bytes]

        modes_len = struct.unpack(">I", container[off:off + 4])[0]
        off += 4
        modes: List[int] = []
        if modes_len > 0:
            modes_bytes = container[off:off + modes_len]
            off += modes_len
            modes = [int(b) for b in modes_bytes]

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

        return blocks, scales, zeros, perm, modes, semantic_mode

    # -----------------------------
    # Public API
    # -----------------------------

    def encode(self, payload_bytes: bytes) -> bytes:
        payload_bytes, perm, semantic_mode = self._semantic_forward_auto(payload_bytes)
        self._auto_tune_shape(len(payload_bytes))

        blocks = self._to_blocks(payload_bytes)
        regions = self._group_blocks(blocks)

        collapsed_all: List[BitDropCollapseEngineV2.BinaryBlock3D] = []
        scales_all: List[float] = []
        zeros_all: List[float] = []
        modes_all: List[int] = []

        for region in regions:
            clusters = self._cluster_blocks(region)
            for cid, cblocks in clusters.items():
                if not cblocks:
                    continue

                self._assign_block_tags(cblocks, cluster_id=cid)
                tmpl = self._build_templates_for_cluster(cluster_id=cid)
                mask = self._build_adjacency_mask(cblocks)
                self._prune_unused_rules(cblocks, tmpl)
                collapsed = self._collapse_cluster(cblocks, mask)
                stabilized = self._stabilize(collapsed)

                vmin, vmax = self._cluster_value_range(stabilized)

                for b in stabilized:
                    mode = self._choose_mode_for_block(b.data)
                    tdata = self._apply_mode_forward(b.data, mode)
                    qb = self.BinaryBlock3D(
                        data=tdata,
                        shape=b.shape,
                        tags=b.tags,
                        index=b.index,
                    )
                    qdata, scale, zero = self._quantize_block_with_range(qb, vmin, vmax)
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
                    modes_all.append(mode)

        if self.use_4d_pairs and collapsed_all:
            pair_map = self._pair_metrics_map(collapsed_all)
            if pair_map:
                keyed = []
                for b, s, z, m in zip(collapsed_all, scales_all, zeros_all, modes_all):
                    k = self._pair_signature_for_block(b, pair_map)
                    keyed.append((k, b, s, z, m))
                keyed.sort(key=lambda t: (t[0], t[1].index))
                collapsed_all = [t[1] for t in keyed]
                scales_all = [t[2] for t in keyed]
                zeros_all = [t[3] for t in keyed]
                modes_all = [t[4] for t in keyed]

        container = self._pack_blocks(
            collapsed_all,
            scales_all,
            zeros_all,
            perm,
            modes_all,
            semantic_mode,
        )
        blob = self._compress(container)
        return blob

    def decode(self, blob: bytes) -> bytes:
        container = self._decompress(blob)
        qblocks, scales, zeros, perm, modes, semantic_mode = self._unpack_blocks(container)

        d0, d1, d2 = self.block_shape
        n_elems = d0 * d1 * d2

        out = bytearray()
        for idx, (b, s, z) in enumerate(zip(qblocks, scales, zeros)):
            data = self._dequantize_block(b.data, s, z, n_elems)
            mode = modes[idx] if idx < len(modes) else 0
            data = self._apply_mode_inverse(data, mode)
            out += data

        out_bytes = bytes(out)
        out_bytes = self._semantic_inverse_with_mode(out_bytes, perm, semantic_mode)
        return out_bytes



















