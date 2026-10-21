from hashlib import sha256
from pathlib import Path
from shutil import copyfile
import json

from PIL import Image, ImageDraw

from backend.document_example import example_docx


ASSETS = Path(__file__).resolve().parent / 'assets' / 'handwriting-sample'
TARGETS = ['confidence', 'visible', 'Success', 'speed']
PARAGRAPHS = [
    ('Title', 'Peer mentoring statement'),
    ('Normal', 'I would bring confidence and patience to the peer mentoring programme. '
     'My first term taught me how much a quiet check-in can matter. I want new students '
     'to have someone they can ask before a small problem becomes a difficult week.'),
    ('Normal', 'I want to notice the visible pressures that students may not raise in a group. '
     'I would start by listening, then agree on one practical next step. Sometimes that '
     'means finding the right person to help rather than trying to solve everything myself.'),
    ('Normal', 'Success is part of learning. A missed deadline or a difficult class should '
     'not end the conversation. I would share what helped me recover from setbacks, '
     'while leaving room for someone whose experience is different from mine.'),
    ('Normal', 'I would value steady progress over speed in these conversations. A useful '
     'meeting should end with a manageable plan and a time to check in again. I can '
     'offer consistency, honest answers, and the willingness to keep listening.'),
]


def create_handwriting_sample() -> dict:
    from backend.documents import LOCK, new_document, document_dir, file_url, save, present

    provenance = json.loads((ASSETS / 'provenance.json').read_text())
    source = provenance['source']
    words = provenance['words']
    if len(words) != len(TARGETS):
        raise ValueError('The handwriting sample assets are incomplete.')
    for name, digest in [(source['path'], source['sha256']),
                         *[(word['inkPath'], word['inkSha256']) for word in words]]:
        path = (ASSETS / name).resolve()
        if not path.is_relative_to(ASSETS) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('The handwriting sample does not match its recorded source assets.')

    with LOCK:
        session = new_document(example_docx(PARAGRAPHS), 'mentoring-statement-draft.docx')
        sid = session['id']
        folder = document_dir(sid)
        sample_folder = folder / 'sample'
        sample_folder.mkdir()
        manifest = session['_manifest']
        page = manifest['pages'][0]
        scale = manifest['scale']
        image = Image.open(folder / 'paper' / page['previewPath']).convert('RGBA')
        expected, placements = [], []
        for before, word in zip(TARGETS, words):
            paragraph = next(item for item in session['paragraphs'] if before in item['text'].split())
            start = paragraph['text'].index(before)
            target = next(item for item in page['words']
                          if item['paragraphId'] == paragraph['id'] and item['start'] == start
                          and item['end'] == start + len(before))
            x, top, width, height = target['rect']
            ink = Image.open(ASSETS / word['inkPath']).convert('RGBA')
            ink_height = round(16 * scale)
            ink_width = round(ink.width * ink_height / ink.height)
            ink = ink.resize((ink_width, ink_height), Image.Resampling.LANCZOS)
            left, upper = round(x * scale), round((top - 6) * scale) - ink_height
            image.alpha_composite(ink, (left, upper))
            line_y = round((top + height * 0.47) * scale)
            ImageDraw.Draw(image).line((round((x - 1) * scale), line_y,
                                       round((x + width + 1) * scale), line_y),
                                      fill='#a62d38', width=round(1.5 * scale))
            expected.append(dict(paragraphId=paragraph['id'], start=start,
                                 end=start + len(before), before=before,
                                 replacement=word['text'], kind='replace'))
            placements.append(dict(sourceId=word['id'], paragraphId=paragraph['id'],
                                   targetStart=start, targetEnd=start + len(before),
                                   inkRectPixels=[left, upper, ink_width, ink_height]))
        input_path = sample_folder / 'handwriting-input.png'
        image.convert('RGB').save(input_path)
        digest = sha256(input_path.read_bytes()).hexdigest()
        copyfile(ASSETS / source['path'], sample_folder / 'original-source.jpg')
        record = {**provenance, 'assembly': {
            'description': 'Real human letter shapes placed on a generated review page. The strike-through lines are generated.',
            'letterTransform': 'Uniform resize to 16-point height using Lanczos resampling; original ink hue and alpha retained.',
            'placements': placements, 'inputSha256': digest,
            'physicalCapture': False,
        }}
        (sample_folder / 'provenance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        session['sample'] = dict(
            kind='human_ink_composite',
            inputUrl=file_url(sid, 'sample/handwriting-input.png'),
            originalUrl=file_url(sid, 'sample/original-source.jpg'),
            provenanceUrl=file_url(sid, 'sample/provenance.json'),
            sourceUrl=source['url'], licenseUrl=source['licenseUrl'], attribution=source['attribution'],
            description='A fictional mentoring statement with real human handwriting from a photographed page, '
                        'placed on this generated review copy. '
                        'The strike-throughs are generated. This is an assembled test page, not a phone photo of a printed review.',
        )
        session['_sampleExpected'] = expected
        session['_sampleInputHash'] = digest
        save(session)
        return present(session)
