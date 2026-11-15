from functools import lru_cache
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np


MODEL_ID = 'PaddlePaddle/PP-OCRv6_medium_rec_onnx'
MODEL_REVISION = '50c7eacafc52fa7bcf4194e8cd08e46f8558504b'
FILES = {
    'inference.onnx': '9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba',
    'inference.yml': '991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129',
}
ALTERNATIVE_MODEL_ID = 'PaddlePaddle/PP-OCRv6_small_rec_onnx'
ALTERNATIVE_MODEL_REVISION = 'b8f84f0b80c529de40b4fbb3544b84fa7233a513'
ALTERNATIVE_FILES = {
    'inference.onnx': '5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634',
    'inference.yml': 'ab078671bb49f06228eadccd34f1bb501e157f7a047095ffb943ba81512c77d1',
}
METADATA = {
    'model': MODEL_ID,
    'revision': MODEL_REVISION,
    'modelSha256': FILES['inference.onnx'],
    'configSha256': FILES['inference.yml'],
    'preprocessing': 'BGR; height 48 preserving aspect; minimum width 320; right zero padding; scale [-1, 1]',
    'decoder': 'CTC greedy, collapse adjacent repeats and remove blank token',
    'license': 'Apache-2.0',
    'alternative': {
        'model': ALTERNATIVE_MODEL_ID,
        'revision': ALTERNATIVE_MODEL_REVISION,
        'modelSha256': ALTERNATIVE_FILES['inference.onnx'],
        'configSha256': ALTERNATIVE_FILES['inference.yml'],
        'license': 'Apache-2.0',
    },
    'candidateRule': 'Medium primary; at most one distinct small-model alternative for human review',
    'implementationSha256': sha256(Path(__file__).read_bytes()).hexdigest(),
}


class HandwritingCandidates(TypedDict):
    text: str
    alternatives: list[str]
    warning: str | None


def model_files(download: bool = False, alternative: bool = False) -> dict[str, Path]:
    from huggingface_hub import hf_hub_download

    model_id = ALTERNATIVE_MODEL_ID if alternative else MODEL_ID
    revision = ALTERNATIVE_MODEL_REVISION if alternative else MODEL_REVISION
    files = ALTERNATIVE_FILES if alternative else FILES
    paths = {}
    for filename, digest in files.items():
        path = Path(hf_hub_download(model_id, filename, revision=revision,
                                    local_files_only=not download))
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('The local handwriting model does not match its pinned version.')
        paths[filename] = path
    return paths


@lru_cache(maxsize=2)
def _load_model(alternative: bool = False) -> tuple:
    import onnxruntime as ort
    import yaml

    paths = model_files(alternative=alternative)
    config = yaml.safe_load(paths['inference.yml'].read_text())
    characters = ['blank', *config['PostProcess']['character_dict'], ' ']
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(paths['inference.onnx']), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    return session, characters


def _recognize(image: np.ndarray, alternative: bool = False) -> str:
    from onnxruntime.capi.onnxruntime_pybind11_state import (
        EngineError, EPFail, Fail, InvalidArgument, InvalidGraph,
        InvalidProtobuf, NoSuchFile, NotImplemented as UnsupportedOperation, RuntimeException,
    )

    height, width = image.shape[:2]
    target_width = max(320, int(48 * width / height))
    if target_width > 3200:
        raise ValueError('This handwriting line is too wide to read reliably. Enter it manually.')
    resized_width = min(target_width, ceil(48 * width / height))
    resized = cv2.resize(image, (resized_width, 48)).astype(np.float32)
    batch = np.zeros((1, 3, 48, target_width), dtype=np.float32)
    batch[0, :, :, :resized_width] = resized.transpose(2, 0, 1) / 127.5 - 1
    try:
        session, characters = _load_model(alternative)
        output = session.run(None, {session.get_inputs()[0].name: batch})[0]
    except (EngineError, EPFail, Fail, InvalidArgument, InvalidGraph, InvalidProtobuf,
            NoSuchFile, UnsupportedOperation, RuntimeException) as error:
        raise RuntimeError('The local handwriting runtime could not read this image.') from error
    if output.shape[-1] != len(characters):
        raise ValueError('The handwriting model and character dictionary do not match.')
    tokens = output[0].argmax(axis=-1)
    keep = np.ones(tokens.shape, dtype=bool)
    keep[1:] = tokens[1:] != tokens[:-1]
    keep &= tokens != 0
    return ''.join(characters[index] for index in tokens[keep]).strip()


def recognize_handwriting(image: np.ndarray) -> str:
    return _recognize(image)


def recognize_handwriting_candidates(image: np.ndarray) -> HandwritingCandidates:
    text = recognize_handwriting(image)
    try:
        alternative = _recognize(image, alternative=True)
    except (ImportError, OSError, RuntimeError, ValueError):
        return {'text': text, 'alternatives': [],
                'warning': 'The second local handwriting reader is unavailable. Check the primary reading against the source image.'}
    return {'text': text, 'alternatives': [alternative] if alternative and alternative != text else [],
            'warning': None}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Cache both pinned local Word handwriting recognizers.')
    parser.add_argument('--setup', action='store_true', required=True)
    parser.parse_args()
    for alternative in (False, True):
        role = 'alternative' if alternative else 'primary'
        for filename, path in model_files(download=True, alternative=alternative).items():
            print(f'{role} {filename}: {path}')
