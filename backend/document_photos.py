from math import ceil, floor, isfinite
from pathlib import Path
from time import perf_counter

import cv2

from backend.document_ocr import METADATA, recognize_handwriting_candidates
from backend.paper import OCR_LOCK, _read_photo


def prepare_guided_photo(data: bytes, output: Path) -> dict:
    image = _read_photo(data)
    output.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output / 'photo.png'), image):
        raise OSError('Could not save the photograph for review.')
    height, width = image.shape[:2]
    return dict(imageWidth=width, imageHeight=height, previewPath='photo.png')


def read_guided_region(photo_path: Path, rect: list[float], output: Path) -> dict:
    started = perf_counter()
    if len(rect) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float))
                             or not isfinite(value) for value in rect):
        raise ValueError('Select a rectangular handwriting region within the photograph.')
    x, y, width, height = rect
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > 1 + 1e-9 or y + height > 1 + 1e-9):
        raise ValueError('Keep the handwriting region within the photograph.')
    image = cv2.imread(str(photo_path))
    if image is None:
        raise ValueError('The source photograph could not be read. Upload it again.')
    image_height, image_width = image.shape[:2]
    left, top = max(0, floor(x * image_width)), max(0, floor(y * image_height))
    right = min(image_width, ceil((x + width) * image_width))
    bottom = min(image_height, ceil((y + height) * image_height))
    if right - left < 8 or bottom - top < 8:
        raise ValueError('Select a larger handwriting region, at least 8 pixels wide and tall.')
    crop = image[top:bottom, left:right]
    output.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output / 'crop.png'), crop):
        raise OSError('Could not save the handwriting crop for review.')
    try:
        with OCR_LOCK:
            reading = recognize_handwriting_candidates(crop)
    except (ImportError, OSError, RuntimeError, ValueError):
        reading = dict(text='', alternatives=[], warning=(
            'Local handwriting recognition is unavailable. The source crop is kept; '
            'enter its wording during review.'))
    return dict(
        rect=[left / image_width, top / image_height,
              (right - left) / image_width, (bottom - top) / image_height],
        cropPath='crop.png', ocrText=reading['text'], ocrAlternatives=reading['alternatives'],
        recognitionWarning=reading['warning'],
        recognizer={**METADATA, 'cropSelection': 'manual', 'targetSelection': 'manual',
                    'capturePreprocessing': ('EXIF orientation applied; source image limited to 3300 pixels '
                                             'per side; rectangular source-pixel crop; no marker alignment '
                                             'or printed-text subtraction.')},
        captureSeconds=round(perf_counter() - started, 3),
    )
