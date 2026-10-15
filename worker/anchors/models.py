"""Pinned model graphs for the anchor children (track 3A, batch lane only).

MediaPipe ships its landmarkers as ``.task`` files: zip archives of TFLite
graphs. OpenCV's DNN module imports TFLite (``cv2.dnn.readNetFromTFLite``,
opencv-python-headless 4.10, already pinned on every lane), so the official
graphs run with no new package. Each net is read from the archive in memory
(the file on disk is the pinned, sha-checked one).

Every function here is called inside a sidecar CHILD process; nothing runs
at import.
"""

import zipfile

_NETS = {}


def task_members(path):
    """{member name: bytes} of a .task archive."""
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def tflite_net(path, member, threads=0):
    """A cv2.dnn net for ``member`` of the .task at ``path`` (cached per
    process)."""
    key = (path, member)
    net = _NETS.get(key)
    if net is None:
        import cv2
        import numpy as np
        if threads:
            cv2.setNumThreads(int(threads))
        buf = np.frombuffer(task_members(path)[member], np.uint8)
        net = cv2.dnn.readNetFromTFLite(buf)
        _NETS[key] = net
    return net


def outputs(net, blob):
    """{output name: array} for one forward pass of ``blob`` (NCHW)."""
    net.setInput(blob)
    names = net.getUnconnectedOutLayersNames()
    return dict(zip(names, net.forward(names)))


def yunet(path, size, score=0.6, nms=0.3, top_k=50):
    import cv2
    return cv2.FaceDetectorYN.create(path, "", (int(size[0]), int(size[1])),
                                     float(score), float(nms), int(top_k))


def onnx_session(path, threads=0):
    key = ("ort", path, threads)
    sess = _NETS.get(key)
    if sess is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = int(threads)
        so.log_severity_level = 3
        sess = ort.InferenceSession(path, so,
                                    providers=["CPUExecutionProvider"])
        _NETS[key] = sess
    return sess
