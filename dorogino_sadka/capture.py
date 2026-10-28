#!/usr/bin/env python3
"""
Скрипт для захвата фотографий со всех подключённых USB-камер.

Использование:
    python capture.py           — сделать фото со всех камер
    python capture.py --init    — сканировать камеры и создать конфиг
"""

import cv2
import sys
import time
import argparse
import os
from datetime import datetime
from pathlib import Path

from diploma.dorogino_sadka.camera_config import discover_cameras, load_camera_config, init_config


# ─── Константы ───────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PHOTOS_DIR = SCRIPT_DIR / "photos"
WARMUP_FRAMES = 10  # количество кадров для прогрева автоэкспозиции
CAPTURE_DELAY = 2


# ─── Захват с камеры ─────────────────────────────────────────────────────────


def capture_from_camera(device_path, camera_name):
    """
    Открывает камеру, прогревает, делает снимок.
    Возвращает путь к сохранённому файлу или None при ошибке.
    """
    cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)

    if not cap.isOpened():
        print(f"  ❌ Не удалось открыть {device_path}")
        return None

    # Устанавливаем формат MJPG
    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # минимальный буфер

    # Прогрев
    for _ in range(WARMUP_FRAMES):
        cap.read()

    # Захват кадра с повторными попытками
    MAX_RETRIES = 3
    frame = None

    for attempt in range(MAX_RETRIES):
        ret, frame = cap.read()
        if ret and frame is not None and frame.size > 0:
            break
        time.sleep(0.1)  # небольшая пауза перед повтором

    # ВАЖНО: полностью освобождаем камеру
    cap.release()
    cap = None  # явное обнуление

    if frame is None or frame.size == 0:
        print(f"  ❌ Не удалось получить кадр с {camera_name}")
        return None

    # Формируем имя файла
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{camera_name}.jpg"
    filepath = PHOTOS_DIR / filename

    # Сохраняем
    cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return filepath



# ─── Основной поток ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Захват фото с USB-камер",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python capture.py --init    Сканировать камеры и создать конфиг
  python capture.py           Сделать фото со всех камер
        """
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Сканировать камеры и создать/обновить конфигурацию",
    )
    args = parser.parse_args()

    # Режим инициализации
    if args.init:
        init_config()
        return

    # Режим захвата
    PHOTOS_DIR.mkdir(exist_ok=True)

    # Загружаем конфиг
    config = load_camera_config()
    if config is None:
        print("❌ Конфигурация не найдена.")
        print()
        print("💡 Сначала инициализируйте конфигурацию:")
        print("   python capture.py --init")
        sys.exit(1)

    # Сканируем камеры
    print("🔍 Сканирование USB-камер...")
    cameras = discover_cameras()

    if not cameras:
        print("❌ Камеры не найдены!")
        sys.exit(1)

    # Показываем список
    print(f"📷 Найдено {len(cameras)} камер(а):")
    for cam in cameras:
        cam_cfg = config.get(cam["by_path"], {})
        name = cam_cfg.get("name", "???")
        print(f"   • {cam['port']} → {cam['device']} [{name}]")
    print()

    # Последовательный захват с каждой камеры
    success_count = 0
    fail_count = 0

    for idx, cam in enumerate(cameras):
        cam_cfg = config.get(cam["by_path"], {})
        name = cam_cfg.get("name", "unknown")

        print(f"  📸 [{idx+1}/{len(cameras)}] Снимок с {name}...", end=" ")
        filepath = capture_from_camera(cam["device"], name)

        if filepath:
            print(f"→ {filepath.name} ✓")
            success_count += 1
        else:
            fail_count += 1

        # Задержка между камерами (кроме последней)
        if idx < len(cameras) - 1:
            time.sleep(CAPTURE_DELAY)

    # Итог
    print(f"\n✅ Готово! Сделано {success_count} снимков.", end="")
    if fail_count:
        print(f" Ошибок: {fail_count}.", end="")
    print()


if __name__ == "__main__":
    main()
