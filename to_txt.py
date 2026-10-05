import os
from pathlib import Path
from PyPDF2 import PdfReader


def pdf_to_txt(pdf_path: str, output_path: str = None) -> str:
    """
    Конвертирует PDF файл в текстовый файл.
    
    Args:
        pdf_path: Путь к PDF файлу
        output_path: Путь для сохранения TXT файла (опционально)
    
    Returns:
        Путь к созданному TXT файлу
    """
    pdf_path = Path(pdf_path)
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"Файл не найден: {pdf_path}")
    
    if not pdf_path.suffix.lower() == '.pdf':
        raise ValueError(f"Файл не является PDF: {pdf_path}")
    
    # Если output_path не указан, создаем TXT файл рядом с PDF
    if output_path is None:
        output_path = pdf_path.with_suffix('.txt')
    else:
        output_path = Path(output_path)
    
    # Читаем PDF
    reader = PdfReader(str(pdf_path))
    text_content = []
    
    for page_num, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        if text.strip():
            text_content.append(f"--- Страница {page_num} ---\n{text}\n")
    
    # Записываем в TXT файл
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(text_content))
    
    return str(output_path)


def convert_directory(directory: str, output_dir: str = None) -> list:
    """
    Конвертирует все PDF файлы в директории.
    
    Args:
        directory: Путь к директории с PDF файлами
        output_dir: Путь для сохранения TXT файлов (опционально)
    
    Returns:
        Список путей к созданным TXT файлам
    """
    directory = Path(directory)
    
    if not directory.is_dir():
        raise NotADirectoryError(f"Директория не найдена: {directory}")
    
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
    
    pdf_files = list(directory.glob('*.pdf')) + list(directory.glob('*.PDF'))
    converted_files = []
    
    print(f"Найдено PDF файлов: {len(pdf_files)}")
    
    for pdf_file in pdf_files:
        try:
            if output_dir:
                output_path = output_dir / pdf_file.with_suffix('.txt').name
            else:
                output_path = None
            
            result = pdf_to_txt(pdf_file, output_path)
            converted_files.append(result)
            print(f"✓ Конвертирован: {pdf_file.name}")
            
        except Exception as e:
            print(f"✗ Ошибка при конвертации {pdf_file.name}: {e}")
    
    return converted_files


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Конвертация PDF в TXT')
    parser.add_argument('input', help='Путь к PDF файлу или директории')
    parser.add_argument('-o', '--output', help='Путь для сохранения результата')
    parser.add_argument('-d', '--directory', action='store_true', 
                       help='Обработать все PDF файлы в директории')
    
    args = parser.parse_args()
    
    try:
        if args.directory:
            results = convert_directory(args.input, args.output)
            print(f"\nУспешно конвертировано файлов: {len(results)}")
        else:
            result = pdf_to_txt(args.input, args.output)
            print(f"✓ Файл сохранен: {result}")
            
    except Exception as e:
        print(f"Ошибка: {e}")
        exit(1)