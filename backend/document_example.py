from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree
from PIL import Image, ImageDraw, ImageFont
import reportlab
from reportlab.pdfbase import pdfmetrics

from backend.document_capture import illustrate_document_page
from backend.docx_engine import inspect_docx


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
PACKAGE = 'http://schemas.openxmlformats.org/package/2006/relationships'
OFFICE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CONTENT = 'http://schemas.openxmlformats.org/package/2006/content-types'
XML = 'http://www.w3.org/XML/1998/namespace'


def _xml(element: etree._Element) -> bytes:
    return etree.tostring(element, xml_declaration=True, encoding='UTF-8', standalone=True)


def _word(parent: etree._Element, tag: str, **attributes: str) -> etree._Element:
    return etree.SubElement(parent, f'{{{W}}}{tag}',
                            {f'{{{W}}}{key}': value for key, value in attributes.items()})


def example_docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """Create the example document from (style, text) paragraphs."""
    document = etree.Element(f'{{{W}}}document', nsmap={'w': W})
    body = _word(document, 'body')
    for style, text in paragraphs:
        paragraph = _word(body, 'p')
        properties = _word(paragraph, 'pPr')
        _word(properties, 'pStyle', val=style)
        run = _word(paragraph, 'r')
        node = _word(run, 't')
        node.set(f'{{{XML}}}space', 'preserve')
        node.text = text
    section = _word(body, 'sectPr')
    _word(section, 'pgSz', w='12240', h='15840')
    _word(section, 'pgMar', top='1320', right='1440', bottom='1320', left='1440',
          header='720', footer='720', gutter='0')

    styles = etree.Element(f'{{{W}}}styles', nsmap={'w': W})
    for name, size, color in [('Normal', '23', '282A29'), ('Title', '48', '202421'),
                              ('Subtitle', '19', '69716A')]:
        style = _word(styles, 'style', type='paragraph', styleId=name)
        if name == 'Normal':
            style.set(f'{{{W}}}default', '1')
        _word(style, 'name', val=name)
        if name != 'Normal':
            _word(style, 'basedOn', val='Normal')
            _word(style, 'next', val='Normal')
        _word(style, 'qFormat')
        paragraph_properties = _word(style, 'pPr')
        _word(paragraph_properties, 'spacing', after='220' if name != 'Subtitle' else '420',
              line='300', lineRule='auto')
        if name in ('Title', 'Subtitle'):
            _word(paragraph_properties, 'keepNext')
        run_properties = _word(style, 'rPr')
        _word(run_properties, 'rFonts', ascii='Georgia', hAnsi='Georgia', cs='Georgia')
        _word(run_properties, 'sz', val=size)
        _word(run_properties, 'szCs', val=size)
        _word(run_properties, 'color', val=color)
        if name == 'Title':
            _word(run_properties, 'b')

    relationships = etree.Element(f'{{{PACKAGE}}}Relationships', nsmap={None: PACKAGE})
    etree.SubElement(relationships, f'{{{PACKAGE}}}Relationship', Id='rId1',
                     Type=f'{OFFICE}/officeDocument', Target='word/document.xml')
    etree.SubElement(relationships, f'{{{PACKAGE}}}Relationship', Id='rId2',
                     Type='http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties',
                     Target='docProps/core.xml')
    document_relationships = etree.Element(f'{{{PACKAGE}}}Relationships', nsmap={None: PACKAGE})
    etree.SubElement(document_relationships, f'{{{PACKAGE}}}Relationship', Id='rId1',
                     Type=f'{OFFICE}/styles', Target='styles.xml')

    types = etree.Element(f'{{{CONTENT}}}Types', nsmap={None: CONTENT})
    etree.SubElement(types, f'{{{CONTENT}}}Default', Extension='rels',
                     ContentType='application/vnd.openxmlformats-package.relationships+xml')
    etree.SubElement(types, f'{{{CONTENT}}}Default', Extension='xml', ContentType='application/xml')
    for part, content_type in [
        ('/word/document.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),
        ('/word/styles.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'),
        ('/docProps/core.xml', 'application/vnd.openxmlformats-package.core-properties+xml'),
    ]:
        etree.SubElement(types, f'{{{CONTENT}}}Override', PartName=part, ContentType=content_type)
    core_namespace = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
    dc = 'http://purl.org/dc/elements/1.1/'
    core = etree.Element(f'{{{core_namespace}}}coreProperties', nsmap={'cp': core_namespace, 'dc': dc})
    etree.SubElement(core, f'{{{dc}}}title').text = next((text for style, text in paragraphs if style == 'Title'), 'Review example')
    etree.SubElement(core, f'{{{dc}}}creator').text = 'PaperPatch example'
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, element in [('[Content_Types].xml', types), ('_rels/.rels', relationships),
                              ('word/document.xml', document), ('word/styles.xml', styles),
                              ('word/_rels/document.xml.rels', document_relationships),
                              ('docProps/core.xml', core)]:
            archive.writestr(name, _xml(element))
    return output.getvalue()


def _ink_bounds(page: dict, edit: dict) -> tuple[float, float, float, float]:
    words = [word for word in page['words'] if word['paragraphId'] == edit['paragraphId']
             and word['start'] < edit['end'] and word['end'] > edit['start']]
    left = min(word['rect'][0] for word in words)
    right = max(word['rect'][0] + word['rect'][2] for word in words)
    top = min(word['rect'][1] for word in words)
    bottom = max(word['rect'][1] + word['rect'][3] for word in words)
    first, last = words[0], words[-1]
    if edit['start'] > first['start']:
        left += pdfmetrics.stringWidth(first['text'][:edit['start'] - first['start']],
                                       'PaperPatchDocument', first['rect'][3])
    if edit['end'] < last['end']:
        right -= pdfmetrics.stringWidth(last['text'][edit['end'] - last['start']:],
                                        'PaperPatchDocument', last['rect'][3])
    return left, top, right, bottom


def _prepared_page(manifest: dict, page: dict, edits: list[dict], paper_dir: Path,
                   output: Path) -> list[dict]:
    illustrate_document_page(manifest, page['number'], edits, paper_dir, output / 'review.png')
    image = Image.open(output / 'review.png').convert('RGB')
    draw = ImageDraw.Draw(image)
    scale = manifest['scale']
    for edit in edits:
        left, top, right, bottom = _ink_bounds(page, edit)
        mid = top + (bottom - top) * 0.46
        draw.line(((left - 1) * scale, mid * scale, right * scale, (mid - 0.5) * scale),
                  fill='#b82e2c', width=2 * scale)
    image.save(output / 'review.png')
    crops = []
    for index, edit in enumerate(edits):
        left, top, right, bottom = _ink_bounds(page, edit)
        if edit['replacement']:
            right = max(right, left + len(edit['replacement']) * 9)
        x1, y1 = max(0, round((left - 7) * scale)), max(0, round((top - 29) * scale))
        x2, y2 = min(image.width, round((right + 9) * scale)), min(image.height, round((bottom + 7) * scale))
        crop_name = f'edit-{index + 1}.png'
        image.crop((x1, y1, x2, y2)).save(output / crop_name)
        crops.append({'cropPath': crop_name,
                      'rect': [x1 / image.width, y1 / image.height,
                               (x2 - x1) / image.width, (y2 - y1) / image.height]})
    return crops


def create_example() -> dict:
    from backend.documents import new_document, document_dir, create_edit, changed, present, file_url, LOCK

    paragraphs = [
        ('Title', 'A night at the observatory'),
        ('Subtitle', 'NORTHLIGHT / COMMUNITY OPEN NIGHT'),
        ('Normal', 'The observatory will open its doors for an evening of stargazing, short talks and warm drinks.'),
        ('Normal', 'Doors open at 6:00 pm. The last entry is at 9:00 pm.'),
        ('Normal', 'Each session is limited to 24 guests and lasts 45 minutes.'),
        ('Normal', 'Visitors should bring a printed ticket to the entrance.'),
        ('Normal', 'The cafe will serve drinks throughout the evening.'),
        ('Normal', 'Please meet the volunteer team at the north entrance.'),
        ('Normal', 'Questions can be sent to hello@northlight.example.'),
    ]
    current_paragraphs = [
        (style, text.replace('9:00 pm', '9:30 pm').replace('24 guests', '30 guests')
         .replace('north entrance', 'east entrance')) for style, text in paragraphs
    ]
    current_paragraphs.insert(3, ('Normal', 'A step-free route is available from the garden gate.'))
    baseline, current = example_docx(paragraphs), example_docx(current_paragraphs)
    prepared = [
        ('Alex', 'p3', '6:00 pm', '6:30 pm'),
        ('Alex', 'p4', '24', '18'),
        ('Alex', 'p5', 'a printed ticket', 'their booking email'),
        ('Alex', 'p6', ' throughout the evening', ''),
        ('Morgan', 'p4', '24', '20'),
    ]
    warning = 'Prepared example: the red ink is illustrative and correction values are supplied for this demonstration. They were not read by OCR.'
    with LOCK:
        session = new_document(baseline, 'northlight-printed-draft.docx')
        sid = session['id']
        path = document_dir(sid)
        (path / 'current.docx').write_bytes(current)
        session.update(demo=True, title='A night at the observatory',
                       currentFilename='northlight-current-draft.docx',
                       currentHash=sha256(current).hexdigest(),
                       currentParagraphs=inspect_docx(current)['paragraphs'])
        session['warnings'] = list(dict.fromkeys(session['warnings'] + [warning]))
        manifest = session['_manifest']
        for reviewer in ('Alex', 'Morgan'):
            edits = []
            for author, paragraph_id, before, replacement in prepared:
                if author != reviewer:
                    continue
                paragraph = next(item for item in session['paragraphs'] if item['id'] == paragraph_id)
                start = paragraph['text'].index(before)
                edits.append({'paragraphId': paragraph_id, 'start': start,
                              'end': start + len(before), 'replacement': replacement})
            for page in manifest['pages']:
                page_edits = [edit for edit in edits if any(
                    word['paragraphId'] == edit['paragraphId'] and word['start'] < edit['end']
                    and word['end'] > edit['start'] for word in page['words'])]
                if not page_edits:
                    continue
                scan_id = uuid4().hex
                output = path / 'scans' / scan_id
                output.mkdir(parents=True)
                crops = _prepared_page(manifest, page, page_edits, path / 'paper', output)
                prefix = f'scans/{scan_id}/'
                source_url = file_url(sid, prefix + 'review.png')
                marks = []
                if reviewer == 'Morgan' and 'p7' in page['paragraphIds']:
                    paragraph = session['paragraphs'][7]
                    start = paragraph['text'].index('north entrance')
                    left, top, right, bottom = _ink_bounds(page, dict(
                        paragraphId='p7', start=start, end=start + len('north entrance')))
                    scale = manifest['scale']
                    image = Image.open(output / 'review.png').convert('RGB')
                    draw = ImageDraw.Draw(image)
                    font_path = Path('/System/Library/Fonts/Supplemental/Apple Chancery.ttf')
                    if not font_path.exists():
                        font_path = Path(reportlab.__file__).parent / 'fonts' / 'VeraIt.ttf'
                    font = ImageFont.truetype(str(font_path), 15 * scale)
                    draw.ellipse(((left - 4) * scale, (top - 2) * scale,
                                  (right + 4) * scale, (bottom + 3) * scale),
                                 outline='#b82e2c', width=2 * scale)
                    draw.text((left * scale, (top - 25) * scale), 'west entrance?',
                              fill='#b82e2c', font=font)
                    image.save(output / 'review.png')
                    x1, y1 = round((left - 8) * scale), round((top - 28) * scale)
                    x2, y2 = min(image.width, round((left + 145) * scale)), round((bottom + 8) * scale)
                    image.crop((x1, y1, x2, y2)).save(output / 'unassigned-note.png')
                    marks.append(dict(id='mark-1', resolution=None, editId=None,
                                      rect=[x1 / image.width, y1 / image.height,
                                            (x2 - x1) / image.width, (y2 - y1) / image.height],
                                      cropUrl=file_url(sid, prefix + 'unassigned-note.png'),
                                      reason='Circled text and a question need interpretation. Enter the intended correction or keep this as a note.'))
                session['scans'].append(dict(id=scan_id, filename=f'{reviewer.lower()}-prepared-review.png',
                                             reviewer=reviewer, page=page['number'], alignedUrl=source_url,
                                             originalUrl=source_url, reviewed=False, warnings=[warning],
                                             source='demo', markCount=len(marks), marks=marks,
                                             _hash=sha256((output / 'review.png').read_bytes()).hexdigest()))
                for proposal, crop in zip(page_edits, crops):
                    edit = create_edit(session, proposal['paragraphId'], proposal['start'], proposal['end'],
                                       proposal['replacement'], reviewer, 'demo', scan_id)
                    edit.update(cropUrl=file_url(sid, prefix + crop['cropPath']), rect=crop['rect'],
                                page=page['number'], confirmed=True, decision=None,
                                kind='replace' if proposal['replacement'] else 'delete')
        changed(session)
        return present(session)
