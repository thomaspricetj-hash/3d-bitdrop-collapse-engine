BITDROP V2
UNIFIED 3D COLLAPSE-BASED COMPRESSION ENGINE
GPLv3 OPEN SOURCE PROJECT

OVERVIEW
BitDrop V2 is a next-generation compression engine designed for AI workloads, high-dimensional vectors, mixed JSON structures, and hybrid metadata streams.
It uses a 3D collapse pipeline, cube-metric clustering, entropy-aware ordering, and TurboQuant nibble-packed quantization to achieve extremely high compression ratios.

BitDrop V2 is especially effective as a second-stage compressor applied to TurboVec output.
In this configuration, BitDrop V2 achieves up to 74.85x total compression relative to the original JSON+vector payload.

FEATURES

The BitDrop V2 engine includes:

3D block decomposition

Adaptive block shaping

Cube-metric extraction

Pre-clustering

Hierarchical tagging

Adjacency mask construction

Entropy-aware collapse ordering

TurboQuant 4-bit nibble-packed quantization

Multi-region grouping

Final entropy coding using zlib

All components operate inside a single unified class.

ARCHITECTURE SUMMARY

The BitDrop V2 pipeline processes data in the following order:

Convert payload into 3D blocks

Compute cube metrics for each block

Cluster blocks based on structural similarity

Assign hierarchical tags

Build adjacency masks

Collapse blocks using entropy-aware ordering

Quantize blocks to 4-bit values

Pack two values per byte

Assemble regions

Compress final container with zlib

This design reduces entropy and increases spatial locality, enabling extremely high compression ratios.

BENCHMARK RESULTS

Payload: JSON + metadata + 256 vectors (1536 dimensions)

Results:

Original JSON: 7,626,464 bytes (1.00x)
TurboVec-only: 209,938 bytes (36.33x)
Dual-field JSON+TV: 456,251 bytes (16.71x)
BitDrop V2 (TV-only): 101,887 bytes (74.85x)

BitDrop V2 compresses TurboVec output 2.06x further.
Total compression relative to the original payload: 74.85x.

USE CASES

BitDrop V2 is ideal for:

AI vector storage

Embedding archives

Mixed JSON + binary metadata

Log compression

Offline model telemetry

High-density data transport

LICENSE

BitDrop V2 is released under the GNU General Public License v3 (GPLv3).
You may modify and redistribute this software under the terms of the GPLv3 license.

AUTHOR

Developed by Thomas Price
2026





