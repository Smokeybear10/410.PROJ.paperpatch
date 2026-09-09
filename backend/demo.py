from io import BytesIO
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.datavalidation import DataValidation
from PIL import Image, ImageDraw, ImageFont


def make_demo_workbooks() -> tuple[bytes, bytes]:
    rows = [
        ['EQ-104', 'Cordless drills', 12, 'Workshop', 149],
        ['EQ-205', 'Safety helmets', 24, 'Locker A', 38],
        ['EQ-306', 'Climbing harnesses', 8, 'Locker B', 95],
        ['EQ-407', 'Portable lights', 6, 'Storage', 72],
        ['EQ-508', 'Battery chargers', '=SUM(2,2)', 'Bench', 49],
        ['EQ-609', 'Radio sets', 10, 'Office', 125],
    ]
    def create(current: bool) -> bytes:
        book = Workbook()
        sheet = book.active
        sheet.title = 'Equipment'
        sheet.append(['Asset ID', 'Equipment', 'Quantity', 'Location', 'Unit cost', 'Total value'])
        for original in (list(reversed(rows)) if current else rows):
            row = original.copy()
            if current and row[0] == 'EQ-104':
                row[2] = 10
            if current and row[0] == 'EQ-306':
                row[3] = 'Rig B'
            if current and row[0] == 'EQ-407':
                row[2] = 5
            n = sheet.max_row + 1
            sheet.append(row + [f'=C{n}*E{n}'])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='214C3A')
        for col, width in [('A',16),('B',25),('C',14),('D',18),('E',15),('F',17)]:
            sheet.column_dimensions[col].width = width
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='center')
            sheet.row_dimensions[row[0].row].height = 24
            row[4].number_format = '$#,##0.00'
            row[5].number_format = '$#,##0.00'
        sheet.freeze_panes = 'C2'
        table = Table(displayName='FieldEquipment', ref='A1:F7')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium4', showRowStripes=True)
        sheet.add_table(table)
        validation = DataValidation(type='whole', operator='greaterThanOrEqual', formula1=0)
        sheet.add_data_validation(validation)
        validation.add('C2:C7')
        notes = book.create_sheet('Instructions')
        notes['A1'] = 'This sheet and its formatting should survive every PaperPatch export.'
        notes['A1'].font = Font(bold=True, color='214C3A')
        notes['A3'] = 'Independent digital edit in latest workbook: EQ-306 location is Rig B.'
        notes.column_dimensions['A'].width = 90
        output = BytesIO()
        book.save(output)
        return output.getvalue()
    return create(False), create(True)


def demo_crops(directory: Path, candidates: list[tuple[str, str, str]]) -> list[str]:
    directory.mkdir(exist_ok=True)
    fonts = [Path('/System/Library/Fonts/Supplemental/Georgia Italic.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf')]
    font_path = next((p for p in fonts if p.exists()), None)
    font = ImageFont.truetype(str(font_path), 72) if font_path else ImageFont.load_default(size=72)
    names = []
    for i, (_, _, text) in enumerate(candidates):
        image = Image.new('RGB', (520, 150), '#f7f4eb')
        draw = ImageDraw.Draw(image)
        draw.line((25, 125, 495, 125), fill='#c9c9bd', width=2)
        draw.text((70, 20), text, fill='#254e71', font=font)
        name = f'illustration-{i}.png'
        image.save(directory / name)
        names.append(name)
    return names
