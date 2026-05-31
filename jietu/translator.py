"""OCR + in-place translation using easyocr and deep-translator."""
from __future__ import annotations
import threading
from PyQt6.QtCore import QObject, pyqtSignal
from PIL import Image
import numpy as np

# ── Cached OCR reader (load the model ONCE, reuse for every translation) ──────
_READER = None
_READER_LOCK = threading.Lock()
_LANGS = ["en", "ch_sim"]


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
    """Warm up the OCR model in the background so the first translation is fast."""
    threading.Thread(target=get_reader, daemon=True).start()


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

            reader = get_reader()   # cached — no per-call model reload
            img_np = np.array(self._image)
            results = reader.readtext(img_np)

            translator = GoogleTranslator(source="auto", target=self._target)
            output = []
            for (bbox, text, conf) in results:
                if conf < 0.3 or not text.strip():
                    continue
                try:
                    translated = translator.translate(text)
                except Exception:
                    translated = text
                output.append((bbox, text, translated))

            self.finished.emit(output)
        except Exception as e:
            self.error.emit(str(e))
