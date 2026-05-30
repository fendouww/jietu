"""OCR + in-place translation using easyocr and deep-translator."""
from __future__ import annotations
import threading
from PyQt6.QtCore import QObject, pyqtSignal
from PIL import Image
import numpy as np


class TranslateWorker(QObject):
    """Run OCR + translation in a background thread."""

    finished = pyqtSignal(list)   # list of (bbox, original, translated)
    error = pyqtSignal(str)

    def __init__(self, image: Image.Image, target_lang: str = "zh-CN"):
        super().__init__()
        self._image = image
        self._target = target_lang
        self._reader = None

    def run(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            import easyocr
            from deep_translator import GoogleTranslator

            if self._reader is None:
                self._reader = easyocr.Reader(["en", "ch_sim"], gpu=False, verbose=False)

            img_np = np.array(self._image)
            results = self._reader.readtext(img_np)

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
