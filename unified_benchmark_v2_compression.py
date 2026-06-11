# ============================================================
# unified_benchmark_v2_compression.py
# Unified Benchmark for:
#   - TurboVec (vectors only)
#   - JSON struct (text + metadata)
#   - Dual-field JSON + TurboVec
#   - BitDropCollapseEngineV2 (3D binary over TurboVec output)
# ============================================================

import json
import random
import time
import hashlib

from bitdrop_core.ai.compression.bitdrop_collapse_codec import BitDropCollapseEngineV2
from python_wrapper import PyTurboVecEncoder


def _make_vectors(n, dim):
    return [[random.random() for _ in range(dim)] for _ in range(n)]


def _make_text_block():
    lines = []
    for _ in range(200):
        lines.append("INFO 2026-06-08T12:00:00Z Processing request id=12345")
        lines.append("INFO 2026-06-08T12:00:00Z Processing request id=12345")
        lines.append("DEBUG compute_value(x, y): entering function")
        lines.append("DEBUG compute_value(x, y): exiting function")
        lines.append("WARN retrying operation due to timeout")
        lines.append("")
    return "\n".join(lines)


def _time(fn):
    t0 = time.perf_counter()
    out = fn()
    t1 = time.perf_counter()
    return out, (t1 - t0) * 1000.0


def _sha256(b: bytes):
    return hashlib.sha256(b).hexdigest()[:16]


def run_unified_benchmark():
    print("\n============================================================")
    print(" Unified BitDrop V2 (3D Binary) + TurboVec Benchmark")
    print("============================================================\n")

    dim = 1536
    n_vec = 256

    vectors = _make_vectors(n_vec, dim)
    text = _make_text_block()
    metadata = {"source": "logs+mixed", "entries": 2000, "note": "unified test"}

    payload = {"text": text, "metadata": metadata, "vectors": vectors}
    orig_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    orig_bytes = orig_json.encode("utf-8")
    orig_size = len(orig_bytes)

    print(f"Original JSON size: {orig_size:,} bytes")
    print(f"Original SHA256: { _sha256(orig_bytes) }\n")

    turbovec = PyTurboVecEncoder(dim, bit_width=4)

    bitdrop = BitDropCollapseEngineV2(
        block_shape=(4, 4, 64),
        level=9,
        max_clusters=32,
    )

    # --------------------------------------------------------
    # TurboVec-only (vectors → TurboVec)
    # --------------------------------------------------------
    def turbovec_run():
        tv_raw = turbovec.encode(vectors)
        return bytes(tv_raw) if isinstance(tv_raw, list) else tv_raw

    tv_bytes, tv_time = _time(turbovec_run)
    tv_size = len(tv_bytes)

    print("[TurboVec] Vector compression:")
    print(f"  Size: {tv_size:,} bytes")
    print(f"  Time: {tv_time:.3f} ms")
    print(f"  SHA256: { _sha256(tv_bytes) }\n")

    # --------------------------------------------------------
    # Dual-field (JSON struct + TurboVec)
    # --------------------------------------------------------
    def dualfield():
        struct_json = json.dumps(
            {"text": text, "metadata": metadata},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        struct_bytes = struct_json.encode("utf-8")

        tv_raw = turbovec.encode(vectors)
        tv_bytes2 = bytes(tv_raw) if isinstance(tv_raw, list) else tv_raw

        return struct_bytes + tv_bytes2

    dual_bytes, dual_time = _time(dualfield)
    dual_size = len(dual_bytes)

    print("[Dual-field] JSON + TurboVec (separate):")
    print(f"  Size: {dual_size:,} bytes")
    print(f"  Time: {dual_time:.3f} ms")
    print(f"  SHA256: { _sha256(dual_bytes) }\n")

    # --------------------------------------------------------
    # BitDrop V2 over TurboVec output (TV → 3D collapse)
    # This matches your README: "BitDrop V2 compresses TurboVec’s output even further"
    # --------------------------------------------------------
    def bitdrop_run():
        # Reuse the same TurboVec encoding path
        tv_raw = turbovec.encode(vectors)
        tv_bytes3 = bytes(tv_raw) if isinstance(tv_raw, list) else tv_raw

        blob = bitdrop.encode(tv_bytes3)
        return blob

    bitdrop_blob, bitdrop_time = _time(bitdrop_run)
    bitdrop_size = len(bitdrop_blob)

    print("[BitDrop V2 3D Binary] (TurboVec output → 3D collapse):")
    print(f"  Size: {bitdrop_size:,} bytes")
    print(f"  Time: {bitdrop_time:.3f} ms")
    print(f"  SHA256: { _sha256(bitdrop_blob) }\n")

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------
    print("============================================================")
    print(" Summary")
    print("============================================================")
    print(f"Original:              {orig_size:,} bytes (1.0000x)")
    print(f"TurboVec-only:         {tv_size:,} bytes ({orig_size / tv_size:.4f}x)")
    print(f"Dual-field JSON+TV:    {dual_size:,} bytes ({orig_size / dual_size:.4f}x)")
    print(f"BitDrop V2 (TV-only):  {bitdrop_size:,} bytes ({orig_size / bitdrop_size:.4f}x)")
    print("============================================================\n")


if __name__ == "__main__":
    run_unified_benchmark()

