"""python _depth_child.py WORKER WEIGHTS IN.npy OUT.npy

Depth Anything V2 Small on one RGB picture (uint8 h x w x 3), CPU, in a process of its own so the engine never
imports torch. Writes relative inverse depth (high = near) at the picture's size. The vendored network
(worker/depth_anything) is used as worker/depth_track.py uses it; its preprocessing is done here with numpy and
torch alone, so an environment without OpenCV runs it too."""
import sys
import types

import numpy as np


def main(worker, weights, ip, op):
    try:
        import cv2      # noqa: F401
    except ImportError:          # the network module names cv2 for its own resize; this child never calls it
        class _Stub(types.ModuleType):
            def __getattr__(self, k):
                if k.startswith("__"):
                    raise AttributeError(k)
                return 0
        sys.modules["cv2"] = _Stub("cv2")
    sys.path.insert(0, worker)
    import torch
    import torch.nn.functional as F
    from depth_anything.dpt import DepthAnythingV2
    torch.set_num_threads(4)
    net = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    net.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    net.eval()
    img = np.load(ip)
    h, w = img.shape[:2]
    k = 518 / min(h, w)                                   # lower bound 518, a multiple of 14 (as image2tensor does)
    nh, nw = max(int(round(h * k / 14)) * 14, 14), max(int(round(w * k / 14)) * 14, 14)
    x = torch.from_numpy(img.astype(np.float32) / 255.0).permute(2, 0, 1)[None]
    x = F.interpolate(x, (nh, nw), mode="bicubic", align_corners=False).clamp(0, 1)
    mean = torch.tensor([0.485, 0.456, 0.406])[None, :, None, None]
    std = torch.tensor([0.229, 0.224, 0.225])[None, :, None, None]
    x = (x - mean) / std
    with torch.no_grad():
        d = net.forward(x)
        d = F.interpolate(d[:, None], (h, w), mode="bilinear", align_corners=True)[0, 0]
    np.save(op, d.numpy().astype(np.float32))


if __name__ == "__main__":
    main(*sys.argv[1:5])
