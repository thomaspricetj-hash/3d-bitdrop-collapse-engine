Project: BitDrop V2 + TurboVec Compression System
Author: Thomas Price
Date: June 2026

OVERVIEW

This repository contains the full implementation of a multi‑stage compression system designed for high‑dimensional AI vector data, mixed JSON payloads, logs, and metadata.

The system combines two major components:

TurboVec
A vector‑aware quantization and delta‑encoding engine optimized for large embedding arrays.

BitDrop V2
A reversible 3D binary collapse engine that further compresses TurboVec output using block transforms, clustering, quantization, and structural ordering.

Together, these components achieve extremely high compression ratios on AI workloads, often far beyond traditional compressors.

FEATURES

TurboVec:

4‑bit or 8‑bit quantization

Per‑vector delta encoding

SIMD‑friendly packing

Deterministic output

Suitable for embeddings from 512 to 4096 dimensions

BitDrop V2:

Global semantic transforms (auto‑selected)

3D block partitioning

Pre‑clustering of blocks

Adjacency masks for ordering

TurboQuant 4‑bit nibble packing

4D pair metrics for final ordering

Fully reversible

zlib final entropy coding

Benchmark Suite:

Unified benchmark for JSON, TurboVec, and BitDrop V2

Deterministic vector generation

Payload multiplier for pattern exposure

Timing and SHA256 reporting

DIRECTORY STRUCTURE

bitdrop_core/
ai/
compression/
bitdrop_collapse_codec.py
(BitDrop V2 implementation)

python_wrapper/
PyTurboVecEncoder
(TurboVec Python interface)

benchmarks/
unified_benchmark_v2_compression.py
(Deterministic benchmark with payload multiplier)

docs/
whitepaper.txt
(Technical overview in plain text)

HOW THE SYSTEM WORKS

Step 1: Input JSON is created containing text, metadata, and vector arrays.

Step 2: TurboVec encodes the vectors using quantization and delta transforms.

Step 3: BitDrop V2 compresses the TurboVec output using:

semantic transforms

3D block grouping

clustering

quantization

ordering

zlib

Step 4: The final compressed blob is produced.

The system is fully reversible. BitDrop V2 decodes back to TurboVec output, and TurboVec reconstructs the original vectors.

BENCHMARKING

The benchmark script measures:

TurboVec compression ratio

Dual‑field JSON plus TurboVec

BitDrop V2 compression ratio

Timing for each stage

SHA256 hashes for verification

The PAYLOAD_MULTIPLIER setting allows testing larger synthetic datasets to expose long‑range redundancy.

USE CASES

Embedding storage for vector databases

AI telemetry compression

Log and metadata archival

On‑device AI storage optimization

Offline model distillation

High‑volume inference pipelines

REQUIREMENTS

Python 3.10 or newer
TurboVec Python wrapper
Standard library only for BitDrop V2
No external dependencies required

RUNNING THE BENCHMARK

Run the unified benchmark:

python unified_benchmark_v2_compression.py

Adjust PAYLOAD_MULTIPLIER to test larger datasets.

LICENSE

This project is released under the MIT License unless otherwise specified.

CONTACT

Developer: Thomas Price
Location: Crestwood, KY
Purpose: High‑performance AI compression research

END OF README




