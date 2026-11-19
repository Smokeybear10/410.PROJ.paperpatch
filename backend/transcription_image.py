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
