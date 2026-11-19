from typing import Literal

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.paper import _read_photo


class PagePreparation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)

    rotation: Literal[0, 90, 180, 270] = 0
    straighten: float = Field(default=0, ge=-15, le=15)
    crop: tuple[float, float, float, float] = (0, 0, 1, 1)

    @field_validator('rotation', mode='before')
    @classmethod
    def valid_rotation(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError('Choose a quarter-turn rotation.')
        return value

    @field_validator('crop')
    @classmethod
    def valid_crop(cls, crop: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x, y, width, height = crop
        if (x < 0 or y < 0 or width <= 0 or height <= 0
                or x + width > 1 + 1e-9 or y + height > 1 + 1e-9):
            raise ValueError('Keep the page selection inside the photo.')
        return crop


def prepare_transcription_image(data: bytes, preparation_json: str | None = None) -> tuple[np.ndarray, dict]:
    if preparation_json and len(preparation_json) > 1000:
        raise ValueError('The page preparation settings are too long.')
    preparation = (PagePreparation.model_validate_json(preparation_json)
                   if preparation_json else PagePreparation())
    image = _read_photo(data)
    source_height, source_width = image.shape[:2]
    angle = preparation.rotation + preparation.straighten
    if angle:
        rotated = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).rotate(
            -angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor='white')
        image = cv2.cvtColor(np.asarray(rotated), cv2.COLOR_RGB2BGR)
    height, width = image.shape[:2]
    x, y, crop_width, crop_height = preparation.crop
    left, top = round(x * width), round(y * height)
    right = min(width, round((x + crop_width) * width))
    bottom = min(height, round((y + crop_height) * height))
    if min(right - left, bottom - top) < 24:
        raise ValueError('Select a larger area with readable handwriting.')
    image = image[top:bottom, left:right].copy()
    if max(image.shape[:2]) > 3300:
        scale = 3300 / max(image.shape[:2])
        image = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)),
                           interpolation=cv2.INTER_AREA)
    return image, {
        **preparation.model_dump(mode='json'),
        'coordinateSpace': 'EXIF-oriented image resized to 3300 pixels, rotated clockwise with expanded white bounds, then cropped',
        'sourceWidth': source_width,
        'sourceHeight': source_height,
        'rotatedWidth': width,
        'rotatedHeight': height,
        'cropPixels': [left, top, right - left, bottom - top],
        'originalPreserved': True,
    }


def estimate_transcription_straightening(data: bytes, rotation: int = 0) -> dict:
    rotation = PagePreparation(rotation=rotation).rotation
    image = _read_photo(data)
    if rotation:
        image = cv2.rotate(image, {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[rotation])
    height, width = image.shape[:2]
    if min(height, width) < 24:
        raise ValueError('Upload a larger image with readable handwriting.')
    if max(height, width) > 1200:
        scale = 1200 / max(height, width)
        image = cv2.resize(image, (round(width * scale), round(height * scale)),
                           interpolation=cv2.INTER_AREA)
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep = np.zeros(count, dtype=bool)
    minimum_area = max(3, round(width * height / 180000))
    edge = max(1, round(width * .025))
    for index in range(1, count):
        x, _, w, h, area = stats[index]
        if (area >= minimum_area and w < width * .8 and h < height * .15
                and x > edge and x + w < width - edge):
            keep[index] = True
    mask[~keep[labels]] = 0
    if keep.sum() < 8 or np.count_nonzero(mask) < max(30, width * height * .0003):
        return {'angle': None, 'message': 'Not enough clear writing to estimate the tilt. Use the slider if needed.'}
    source = Image.fromarray(mask)
    scores = []
    for step in range(-40, 41):
        angle = step / 4
        rotated = np.asarray(source.rotate(-angle, Image.Resampling.NEAREST,
                                           expand=True, fillcolor=0))
        profile = np.count_nonzero(rotated, axis=1).astype(float)
        scores.append(float(np.square(profile).sum() / max(1, profile.sum()) ** 2))
    best = max(range(len(scores)), key=lambda index: scores[index])
    angle = (best - 40) / 4
    if best in (0, len(scores) - 1):
        return {'angle': None, 'message': 'The page needs a larger adjustment. Rotate it or use the slider, then check the writing.'}
    if abs(angle) < 1:
        return {'angle': 0, 'message': 'The writing already looks approximately level. Fine adjustments are still available.'}
    if scores[best] < scores[40] * 1.03:
        return {'angle': None, 'message': 'There is no clear writing angle. Use the slider and check the preview.'}
    return {'angle': angle, 'message': 'Suggested angle applied to the preview. Check the writing before reading the page.'}


def transcription_source_mask(image_size: tuple[int, int], preparation: dict | None) -> np.ndarray | None:
    if not preparation or not preparation.get('straighten'):
        return None
    source = Image.new('L', (preparation['sourceWidth'], preparation['sourceHeight']), 255)
    rotated = source.rotate(-(preparation['rotation'] + preparation['straighten']),
                            resample=Image.Resampling.NEAREST, expand=True, fillcolor=0)
    left, top, width, height = preparation['cropPixels']
    cropped = rotated.crop((left, top, left + width, top + height))
    if cropped.size != image_size:
        cropped = cropped.resize(image_size, Image.Resampling.NEAREST)
    return np.asarray(cropped) > 0
