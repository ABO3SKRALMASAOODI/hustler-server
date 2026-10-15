"""Depth Anything V2 (Small) network definition, vendored for the
``depth.small`` sidecar (track 4A; worker/devices/INTERFACES_W4.md §2).

Source: https://github.com/DepthAnything/Depth-Anything-V2, commit
a561b849ebae10a6f5ef49e26c83cbbcd36c71bf, directory depth_anything_v2/
(Apache License 2.0, LICENSE in this directory). The DINOv2 layers keep
Meta's original Apache-2.0 headers. Valmera modifications: the package is
renamed ``depth_anything``; dpt.py defines a local ``Compose`` instead of
importing torchvision (the batch image carries torch, not torchvision).

Weights are NOT here: the batch image bakes the official Small checkpoint
(models/depth_anything_v2_small.json, Apache-2.0; the Base and Large
checkpoints are CC-BY-NC and are refused by checksum). Only the sidecar
child imports this package; it needs torch, which only the batch lane has.
"""
