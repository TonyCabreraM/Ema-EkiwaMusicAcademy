"""
Convierte las tablas del PDF 'INVENTARIO SEPTIEMBRE 2026.pdf' a Excel.

Estructura del PDF (4 páginas, 4 tablas):
  1. EXISTENCIAS EN BARRA JÚBILO   -> ARTÍCULO | UNIDAD
  2. ENTRADAS ALMACÉN JÚBILO       -> ARTÍCULO | 02/09/26 | 03/09/26 | TOTAL | UNIDAD
  3. SALIDAS DE ALMACÉN JÚBILO     -> ARTÍCULO | 02/09/26 | 04/09/26 | TOTAL | UNIDAD
  4. EXISTENCIAS ALMACÉN           -> ARTÍCULO | PRESENTACIÓN | MÍNIMO | MÁXIMO | EXISTENCIAS | UNIDAD
"""
import pdfplumber
import pandas as pd
import re

PDF_PATH = 'INVENTARIO SEPTIEMBRE 2026.pdf'
EXCEL_PATH = 'INVENTARIO SEPTIEMBRE 2026.xlsx'

# Límites de columnas por página (basados en análisis de coordenadas del PDF)
# Cada entrada: (nombre_columna, x_min, x_max)
PAGE_COLUMNS = {
    1: [
        ('ARTÍCULO', 0, 500),
        ('UNIDAD', 500, 600),
    ],
    2: [
        ('ARTÍCULO', 0, 120),
        ('02/09/26', 120, 150),
        ('03/09/26', 150, 500),
        ('TOTAL', 500, 530),
        ('UNIDAD', 530, 600),
    ],
    3: [
        ('ARTÍCULO', 0, 95),
        ('FECHAS', 95, 500),   # se separa en 02/09/26 y 04/09/26 después
        ('TOTAL', 500, 540),
        ('UNIDAD', 540, 600),
    ],
    4: [
        ('ARTÍCULO', 0, 180),
        ('PRESENTACIÓN', 180, 260),
        ('MÍNIMO', 260, 300),
        ('MÁXIMO', 300, 340),
        ('EXISTENCIAS', 340, 380),
        ('UNIDAD', 380, 600),
    ],
}

# Títulos de cada tabla (para las hojas de Excel)
PAGE_TITLES = {
    1: 'EXISTENCIAS EN BARRA JÚBILO',
    2: 'ENTRADAS ALMACÉN JÚBILO',
    3: 'SALIDAS DE ALMACÉN JÚBILO',
    4: 'EXISTENCIAS ALMACÉN',
}

# Palabras que inician filas de encabezado/título a ignorar
IGNORE_START_WORDS = ('EXISTENCIAS', 'ENTRADAS', 'SALIDAS', 'ARTÍCULO', 'OBSERVACIONES',
                      'ORDENAR', 'PRODUCTO', 'PANADERÍA', 'TOTAL', 'UNIDAD', 'PRESENTACIÓN',
                      'MÍNIMO', 'MÁXIMO', '02/09/26', '03/09/26', '04/09/26')

# Columnas que NO se deduplican (página 3: las fechas pueden repetir valor legítimamente)
NO_DEDUPE = {'FECHAS'}


def group_words_by_line(words, y_tol=3.0):
    """Agrupa palabras en líneas según su coordenada top."""
    lines = []
    for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
        placed = False
        for line in lines:
            if abs(line['top'] - w['top']) <= y_tol:
                line['words'].append(w)
                placed = True
                break
        if not placed:
            lines.append({'top': w['top'], 'words': [w]})
    for line in lines:
        line['words'].sort(key=lambda w: w['x0'])
    lines.sort(key=lambda l: l['top'])
    return lines


def assign_column(word, columns):
    """Asigna una palabra a un índice de columna según su x0."""
    x = word['x0']
    for i, (name, xmin, xmax) in enumerate(columns):
        if xmin <= x < xmax:
            return i
    # Si no cae en ninguna columna, asignar a la más cercana
    best = 0
    best_dist = float('inf')
    for i, (name, xmin, xmax) in enumerate(columns):
        dist = min(abs(x - xmin), abs(x - xmax))
        if dist < best_dist:
            best_dist = dist
            best = i
    return best


def dedupe_values(values):
    """Elimina valores duplicados consecutivos (artefacto de centrado vertical)."""
    if not values:
        return ''
    result = [values[0]]
    for v in values[1:]:
        if v != result[-1]:
            result.append(v)
    return ' '.join(result)


def extract_page(page_num, page):
    """Extrae las filas de una página como lista de dicts {columna: valor}."""
    columns = PAGE_COLUMNS[page_num]
    words = page.extract_words()
    lines = group_words_by_line(words)

    rows = []
    current = None  # fila en construcción

    for line in lines:
        line_words = line['words']
        # Separar palabras de artículo vs valores
        article_words = [w for w in line_words if w['x0'] < columns[0][2]]
        value_words = [w for w in line_words if w['x0'] >= columns[0][2]]

        if article_words:
            # Nueva fila
            if current is not None:
                rows.append(current)
            current = {name: [] for name, _, _ in columns}
            # Asignar palabras de artículo
            for w in article_words:
                idx = assign_column(w, columns)
                current[columns[idx][0]].append(w['text'])
            # Asignar valores de esta línea
            for w in value_words:
                idx = assign_column(w, columns)
                current[columns[idx][0]].append(w['text'])
        else:
            # Línea solo con valores -> continuar fila actual
            if current is not None:
                for w in value_words:
                    idx = assign_column(w, columns)
                    current[columns[idx][0]].append(w['text'])

    if current is not None:
        rows.append(current)

    # Convertir a filas finales
    result = []
    for r in rows:
        row = {}
        for name, _, _ in columns:
            if name in NO_DEDUPE:
                row[name] = ' '.join(r[name])
            else:
                row[name] = dedupe_values(r[name])
        result.append(row)
    return result


def is_garbage_article(articulo):
    """Detecta filas basura (fragmentos de fecha rotada de la página 3)."""
    if '/' in articulo:
        return True
    if len(articulo) <= 1:
        return True
    if re.fullmatch(r'[\d/ ]+', articulo):
        return True
    return False


def clean_rows(rows, page_num):
    """Limpia las filas extraídas."""
    cleaned = []
    for r in rows:
        articulo = r.get('ARTÍCULO', '').strip()
        # Ignorar filas vacías o de encabezado
        if not articulo:
            continue
        if any(articulo.startswith(w) for w in IGNORE_START_WORDS):
            continue
        if is_garbage_article(articulo):
            continue

        # Página 1: AGUA MINERAL SIFÓN no tiene unidad en el PDF (se completa con PIEZA,
        # consistente con la tabla de EXISTENCIAS ALMACÉN)
        if page_num == 1 and articulo == 'AGUA MINERAL SIFÓN' and not r.get('UNIDAD'):
            r['UNIDAD'] = 'PIEZA'

        # Página 3: separar las fechas 02/09/26 y 04/09/26 por orden de lectura
        if page_num == 3:
            fechas = r.pop('FECHAS', '')
            parts = fechas.split()
            r['02/09/26'] = parts[0] if len(parts) >= 1 else ''
            r['04/09/26'] = parts[1] if len(parts) >= 2 else ''

        # Página 4: quitar la nota "PRODUCTO AGOTADO" que se pegó a la unidad
        if page_num == 4:
            unidad = r.get('UNIDAD', '')
            r['UNIDAD'] = unidad.replace('PRODUCTO AGOTADO', '').strip()

        # Página 4: fusionar 'AGUA MINERAL' + 'SIFON' (nombre largo que invade la columna)
        if page_num == 4 and articulo == 'AGUA MINERAL' and r.get('PRESENTACIÓN') == 'SIFON':
            r['ARTÍCULO'] = 'AGUA MINERAL SIFON'
            r['PRESENTACIÓN'] = ''

        cleaned.append(r)
    return cleaned


def main():
    pdf = pdfplumber.open(PDF_PATH)
    all_data = {}

    for i, page in enumerate(pdf.pages):
        page_num = i + 1
        rows = extract_page(page_num, page)
        rows = clean_rows(rows, page_num)
        all_data[page_num] = rows

        print(f'=== {PAGE_TITLES[page_num]} ({len(rows)} filas) ===')
        for r in rows:
            print(r)
        print()

    # Escribir Excel con una hoja por tabla
    with pd.ExcelWriter(EXCEL_PATH, engine='openpyxl') as writer:
        for page_num, rows in all_data.items():
            df = pd.DataFrame(rows)
            sheet_name = PAGE_TITLES[page_num][:31]  # Excel limita a 31 caracteres
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f'✅ Excel creado: {EXCEL_PATH}')
    print('Hojas:', [PAGE_TITLES[p][:31] for p in all_data])


if __name__ == '__main__':
    main()