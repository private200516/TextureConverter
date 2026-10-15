"""Source-resolution edit/protection masks with bounded undo. No image resizing."""
from __future__ import annotations
import zlib
import numpy as np
from PIL import Image


class MaskModel:
    def __init__(self, shape, candidate=None, undo_limit=64 * 1024 * 1024):
        self.shape = tuple(shape[:2])
        self.candidate = self._array(candidate) if candidate is not None else np.zeros(self.shape, np.uint8)
        self.layers = {'edit': self.candidate.copy(), 'protect': np.zeros(self.shape, np.uint8)}
        self.history = []
        self.undo_bytes = 0
        self.undo_limit = undo_limit

    def _array(self, value):
        a = np.asarray(value)
        if a.shape != self.shape:
            raise ValueError(f'Mask dimensions {a.shape} do not match source {self.shape}. No resampling was performed.')
        if a.dtype == np.uint8:
            return a.copy()
        if not np.isfinite(a).all():
            raise ValueError('Mask contains invalid numeric values')
        return np.rint(np.clip(a, 0, 1) * 255).astype(np.uint8)

    def checkpoint(self, layer):
        data = zlib.compress(self.layers[layer].tobytes(), 1)
        if len(data) > self.undo_limit:
            raise ValueError('This edit exceeds the undo memory budget. Export the mask before editing a smaller texture.')
        while self.history and self.undo_bytes + len(data) > self.undo_limit:
            _, old = self.history.pop(0)
            self.undo_bytes -= len(old)
        self.history.append((layer, data))
        self.undo_bytes += len(data)

    def undo(self):
        if not self.history:
            return False
        layer, data = self.history.pop()
        self.undo_bytes -= len(data)
        self.layers[layer] = np.frombuffer(zlib.decompress(data), np.uint8).reshape(self.shape).copy()
        return True

    def brush(self, layer, x, y, radius, erase=False):
        h, w = self.shape
        r = max(1, int(radius))
        x, y = int(x), int(y)
        x0, x1 = max(0, x-r), min(w, x+r+1)
        y0, y1 = max(0, y-r), min(h, y+r+1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        hit = (xx-x)**2 + (yy-y)**2 <= r*r
        self.layers[layer][y0:y1, x0:x1][hit] = 0 if erase else 255

    def action(self, layer, action):
        self.checkpoint(layer)
        if action == 'clear': self.layers[layer].fill(0)
        elif action == 'invert': self.layers[layer] = 255-self.layers[layer]
        elif action == 'restore': self.layers[layer] = self.candidate.copy() if layer == 'edit' else np.zeros(self.shape, np.uint8)
        else: raise ValueError(action)

    def load(self, layer, path):
        with Image.open(path) as im:
            if im.format != 'PNG' or im.mode not in ('L', '1', 'I;16', 'I'):
                raise ValueError('Import a grayscale PNG mask (not RGB/RGBA).')
            if (im.height, im.width) != self.shape:
                raise ValueError(f'Mask {im.width}x{im.height}; source {self.shape[1]}x{self.shape[0]}. Import an exact-size mask; resizing is disabled.')
            data = np.asarray(im)
            if data.dtype.itemsize > 1:
                data = np.rint(data.astype(np.float32) / 257).clip(0, 255).astype(np.uint8)
            elif im.mode == '1': data = data.astype(np.uint8)*255
        self.checkpoint(layer)
        self.layers[layer] = data.astype(np.uint8)

    def save(self, layer, path):
        Image.fromarray(self.layers[layer]).save(path, format='PNG')

    def final(self):
        # Explicit protection wins even over a full-strength manual edit.
        result = self.layers['edit'].astype(np.float32)/255
        result[self.layers['protect'] > 0] = 0
        return result
