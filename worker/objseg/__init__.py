"""Any-object segmentation and depth order (track 3B).

ROADMAP Wave 3 / 3B; contract in worker/devices/INTERFACES_W3.md §1, §3.

  sam.py         EfficientSAM-Ti (opencv_zoo ONNX, Apache-2.0) on
                 onnxruntime: one mask from a box and/or points, run on a
                 square crop round the prompt (small objects keep detail)
  propagate.py   the mask carried through a window: DIS optical flow frame
                 to frame, a SAM re-prompt from the carried box every few
                 frames (accepted only when it agrees: no drift onto the
                 neighbour), a still object held still
  depthorder.py  in front of / behind the person, measured from the mattes
  jobs.py        the ``objects.sam2`` sidecar child

The batch lane runs the model (a sidecar); renders read the mask chunks.
Nothing here imports cv2 or onnxruntime at module level.
"""
