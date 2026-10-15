"""Anchors: where things are in the footage, measured once (track 3A).

ROADMAP Wave 3 / 3A; contract in worker/devices/INTERFACES_W3.md §1, §4.

Measuring (batch-lane sidecar children; models via cv2.dnn / onnxruntime):
  models.py        pinned MediaPipe .task graphs and YuNet, loaded in memory
  roi.py           rotated square crops (the MediaPipe ROI convention)
  faces.py         YuNet + Face Landmarker (478 points, irises) per frame
  hands.py         palm detector + hand landmarks (21 points), tracked
  pose.py          pose landmarks (33 points), ROI from the face / last pose
  points.py        LK point tracks with forward-backward checks
  camera_motion.py matte-free global homography per frame
  jobs.py          the sidecar children for every landmarks/track kind

Reading (every lane; pure numpy + the sidecar store):
  query.py         an anchor target at a source second -> a pose (point,
                   scale, rotation, quad, confidence), smoothed
  smooth.py        the One-Euro filter, gap bridging, confidence

Nothing here imports cv2 at module level: the agent lanes import query.py.
"""
