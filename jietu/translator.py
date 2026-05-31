"""OCR + in-place translation.

OCR backend is chosen by platform:
  macOS → native Vision framework via `ocrmac` (fast, no torch)
  others → easyocr (CPU/GPU)
"""
from __future__ import annotations
import sys
import threading
from PyQt6.QtCore import QObject, pyqtSignal
from PIL import Image
import numpy as np

# ── OCR backend selection ────────────────────────────────────────────────────
_READER = None
_READER_LOCK = threading.Lock()
_LANGS = ["en", "ch_sim"]
_BACKEND = None


def _backend() -> str:
    """'mac' (Vision) or 'easyocr'."""
    global _BACKEND
    if _BACKEND is None:
        if sys.platform == "darwin":
            try:
                import ocrmac  # noqa: F401
                _BACKEND = "mac"
            except Exception:
                _BACKEND = "easyocr"
        else:
            _BACKEND = "easyocr"
    return _BACKEND


def _gpu_available() -> bool:
    """True if a CUDA GPU is usable (falls back to CPU otherwise)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def get_reader():
    """Return a shared easyocr.Reader, building it on first use (thread-safe)."""
    global _READER
    if _READER is None:
        with _READER_LOCK:
            if _READER is None:
                import easyocr
                _READER = easyocr.Reader(
                    _LANGS, gpu=_gpu_available(), verbose=False
                )
    return _READER


def preload():
    """Warm up the OCR model in the background (easyocr only; mac Vision is instant)."""
    if _backend() == "easyocr":
        threading.Thread(target=get_reader, daemon=True).start()


def _run_ocr(image: Image.Image):
    """Return easyocr-style results: list of (bbox_points, text, conf)."""
    if _backend() == "mac":
        return _ocr_mac(image)
    reader = get_reader()
    return reader.readtext(np.array(image))


def _ocr_mac(image: Image.Image):
    """macOS Vision OCR via ocrmac → easyocr-style boxes (top-left, pixels)."""
    from ocrmac import ocrmac
    W, H = image.size
    out = []
    annotations = ocrmac.OCR(
        image, recognition_level="accurate",
        language_preference=["en-US", "zh-Hans"],
    ).recognize()
    for text, conf, (x, y, w, h) in annotations:
        # ocrmac boxes are normalized (0-1), origin bottom-left.
        px0 = x * W
        pw = w * W
        ph = h * H
        py0 = (1.0 - y - h) * H
        bbox = [[px0, py0], [px0 + pw, py0],
                [px0 + pw, py0 + ph], [px0, py0 + ph]]
        out.append((bbox, text, conf))
    return out


class TranslateWorker(QObject):
    """Run OCR + translation in a background thread."""

    finished = pyqtSignal(list)   # list of (bbox, original, translated)
    error = pyqtSignal(str)

    def __init__(self, image: Image.Image, target_lang: str = "zh-CN"):
        super().__init__()
        self._image = image
        self._target = target_lang

    def run(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            from deep_translator import GoogleTranslator

            results = _run_ocr(self._image)

            # Merge word/line fragments into full-line segments so each
            # translation has sentence context (no more isolated words).
            segments = _merge_lines(results)

            translator = GoogleTranslator(source="auto", target=self._target)
            texts = [s[4] for s in segments]
            try:
                translations = translator.translate_batch(texts) if texts else []
            except Exception:
                translations = [self._safe_translate(translator, t) for t in texts]

            output = []
            for (x0, y0, x1, y1, text), zh in zip(segments, translations):
                bbox = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                output.append((bbox, text, zh or text))

            self.finished.emit(output)
        except Exception as e:
            self.error.emit(str(e))

    @staticmethod
    def _safe_translate(translator, text):
        try:
            return translator.translate(text)
        except Exception:
            return text


def _merge_lines(results):
    """Group OCR fragments into same-line segments (full phrases).

    Fragments are clustered by vertical center; within a line they're joined
    left-to-right, but a large horizontal gap starts a new segment so separate
    columns don't get merged together.
    """
    items = []
    for bbox, text, conf in results:
        if conf < 0.3 or not (text and text.strip()):
            continue
        xs = [float(p[0]) for p in bbox]
        ys = [float(p[1]) for p in bbox]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        items.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cy": (y0 + y1) / 2, "h": max(1.0, y1 - y0),
            "text": text.strip(),
        })
    items.sort(key=lambda i: (i["cy"], i["x0"]))

    lines = []
    for it in items:
        placed = False
        for ln in lines:
            if abs(it["cy"] - ln["cy"]) <= 0.6 * max(it["h"], ln["h"]):
                ln["items"].append(it)
                ln["cy"] = sum(j["cy"] for j in ln["items"]) / len(ln["items"])
                ln["h"] = max(ln["h"], it["h"])
                placed = True
                break
        if not placed:
            lines.append({"cy": it["cy"], "h": it["h"], "items": [it]})

    segments = []
    for ln in lines:
        its = sorted(ln["items"], key=lambda i: i["x0"])
        run = [its[0]]
        for prev, cur in zip(its, its[1:]):
            gap = cur["x0"] - prev["x1"]
            if gap > 1.2 * max(prev["h"], cur["h"]):
                segments.append(run)
                run = [cur]
            else:
                run.append(cur)
        segments.append(run)

    merged = []
    for run in segments:
        text = " ".join(r["text"] for r in run)
        x0 = int(min(r["x0"] for r in run))
        y0 = int(min(r["y0"] for r in run))
        x1 = int(max(r["x1"] for r in run))
        y1 = int(max(r["y1"] for r in run))
        merged.append((x0, y0, x1, y1, text))
    return merged
