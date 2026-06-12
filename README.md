Project: 3D BitDrop Collapse Engine
Author: Thomas Price
Year: 2026

DESCRIPTION

The 3D BitDrop Collapse Engine is a high density binary compression system designed to operate on structured byte streams such as TurboVec output. The engine uses 3D block geometry, metric driven clustering, shared range quantization, and deterministic collapse ordering to produce highly compressible binary layouts while remaining fully lossless.

This project contains the full implementation of BitDrop V2, including:
3D block partitioning
Cube metric analysis
Region grouping
Cluster formation
Shared range 4 bit quantization
Constraint driven collapse ordering
4D pairwise ordering layer
Final binary packing and entropy coding

The engine is optimized for vectorized data and achieves extremely high compression ratios when used after TurboVec.

FEATURES

3D Block Partitioning
Converts linear byte streams into structured 3D blocks for improved pattern detection.

Cube Metrics
Computes non zero count, mean, variance approximation, and edge energy for each block.

Region Grouping
Sorts blocks into regions based on banded metrics to improve cluster coherence.

Clustering
Assigns blocks to clusters using a hash based signature. Clusters share quantization ranges.

Shared Range Quantization
All blocks in a cluster quantize into a common value range using 4 bit nibble packing.

Collapse Ordering
Blocks are ordered using adjacency constraints and complexity scoring.

4D Pairwise Ordering
Final ordering step that improves global locality without affecting cluster structure.

Lossless Reconstruction
The engine preserves exact byte level reconstruction.

PERFORMANCE

Tested on TurboVec compressed JSON data (7.6 MB original).

Results:
TurboVec output: 209,938 bytes
BitDrop V2 output: 101,030 bytes
Compression ratio: 75.48x

BitDrop V2 consistently achieves 75x to 76x compression on this data profile.

LIMITATIONS

Performance depends on the structure of the input data.
Block depth changes provide only small improvements.
RLE and similar transforms do not improve compression.
Achieving 80x requires a new stage such as residual coding or a custom entropy coder.

FUTURE WORK

Residual coding layer
Custom entropy coder (rANS or arithmetic)
Adaptive block geometry
Learned predictive model for residuals

USAGE

The main compressor class is located in:

bitdrop_collapse_codec.py

To use the compressor:

Import the engine

Create an instance of BitDropCollapseEngineV2

Call encode() to compress

Call decode() to decompress

Example:

engine = BitDropCollapseEngineV2()
compressed = engine.encode(data)
restored = engine.decode(compressed)

LICENSE

This project is owned and maintained by Thomas Price.

END OF README





