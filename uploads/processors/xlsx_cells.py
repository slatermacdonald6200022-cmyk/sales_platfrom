"""Точечная запись чисел без пересохранения оформления и расширений Excel."""
import math
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def write_quantity_cells(source, destination, updates):
    with zipfile.ZipFile(source) as original:
        book = ET.fromstring(original.read('xl/workbook.xml'))
        rels = ET.fromstring(original.read('xl/_rels/workbook.xml.rels'))
        paths = {node.attrib['Id']: posixpath.normpath(posixpath.join('xl', node.attrib['Target']))
                 if not node.attrib['Target'].startswith('/') else node.attrib['Target'].lstrip('/')
                 for node in rels}
        changed = {}
        for sheet in book.find('s:sheets', NS):
            cells = updates.get(sheet.attrib['name'], {})
            if not cells:
                continue
            path = paths[sheet.attrib['{' + NS['r'] + '}id']]
            xml = original.read(path).decode('utf-8')
            prefix_match = re.search(r'<([\w]+:)?worksheet\b', xml)
            prefix = prefix_match.group(1) or ''
            tag = re.escape(prefix)
            for address, value in cells.items():
                if not math.isfinite(value):
                    raise ValueError('Факт должен быть конечным числом.')
                pattern = r'<' + tag + r'c\b[^>]*\br="' + re.escape(address) + r'"[^>]*(?:/>|>.*?</' + tag + r'c>)'
                match = re.search(pattern, xml, flags=re.S)
                if match:
                    old = match.group()
                    opening = old[:old.index('>') + 1].rstrip('/>')
                    opening = re.sub(r'\s+t="[^"]*"', '', opening)
                    replacement = opening + f'><{prefix}v>' + repr(float(value)) + f'</{prefix}v></{prefix}c>'
                    xml = xml[:match.start()] + replacement + xml[match.end():]
                else:
                    row_number = re.search(r'\d+', address).group()
                    row_pattern = r'(<' + tag + r'row\b[^>]*\br="' + row_number + r'"[^>]*>)(.*?)(</' + tag + r'row>)'
                    row_match = re.search(row_pattern, xml, flags=re.S)
                    if not row_match:
                        raise ValueError(f'Не найдена строка {row_number} в исходной книге.')
                    content = row_match.group(2)
                    column = address.rstrip('0123456789')
                    rank = lambda letters: (len(letters), letters)
                    insertion = len(content)
                    for cell_match in re.finditer(r'<' + tag + r'c\b[^>]*\br="([A-Z]+)\d+"', content):
                        if rank(cell_match.group(1)) > rank(column):
                            insertion = cell_match.start()
                            break
                    new_cell = f'<{prefix}c r="{address}"><{prefix}v>{float(value)!r}</{prefix}v></{prefix}c>'
                    content = content[:insertion] + new_cell + content[insertion:]
                    xml = xml[:row_match.start(2)] + content + xml[row_match.end(2):]
            changed[path] = xml.encode('utf-8')
        workbook_xml = original.read('xl/workbook.xml').decode('utf-8')
        prefix = re.search(r'<([\w]+:)?workbook\b', workbook_xml).group(1) or ''
        tag = re.escape(prefix)
        calc = f'<{prefix}calcPr fullCalcOnLoad="1" forceFullCalc="1" calcMode="auto"/>'
        if re.search(r'<' + tag + r'calcPr\b', workbook_xml):
            workbook_xml = re.sub(r'<' + tag + r'calcPr\b[^>]*(?:/>|>.*?</' + tag + r'calcPr>)', calc, workbook_xml)
        else:
            workbook_xml = workbook_xml.replace(f'</{prefix}workbook>', calc + f'</{prefix}workbook>')
        changed['xl/workbook.xml'] = workbook_xml.encode('utf-8')
        with zipfile.ZipFile(destination, 'w') as output:
            for info in original.infolist():
                output.writestr(info, changed.get(info.filename, original.read(info.filename)))
