"""
Модуль для сканирования USB-камер и управления конфигурацией.
"""

import os
import json
import re
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "cameras.json"


def discover_cameras():
    """
    Сканирует /dev/v4l/by-path/ и возвращает список камер.
    Дедуплицирует по реальному устройству и по USB-порту.
    """
    by_path_dir = "/dev/v4l/by-path"
    cameras = []
    seen_devices = set()
    seen_ports = set()

    if not os.path.isdir(by_path_dir):
        print("⚠️  Директория /dev/v4l/by-path/ не найдена.")
        print("   Возможно, v4l-utils не установлен: sudo apt install v4l-utils")
        return cameras

    for symlink in sorted(os.listdir(by_path_dir)):
        if "usb" not in symlink:
            continue

        full_path = os.path.join(by_path_dir, symlink)
        real_device = os.path.realpath(full_path)

        # Дедупликация 1: по реальному устройству
        if real_device in seen_devices:
            continue
        seen_devices.add(real_device)

        # Извлекаем USB-порт
        port_match = re.search(r'(usb-[\d:.]+)', symlink)
        port = port_match.group(1) if port_match else symlink

        # Дедупликация 2: по USB-порту
        if port in seen_ports:
            continue
        seen_ports.add(port)

        cameras.append({
            "by_path": symlink,
            "port": port,
            "device": real_device,
        })

    return cameras


def save_config(config):
    """Сохраняет конфигурацию в JSON."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def load_camera_config():
    """
    Загружает cameras.json.
    Возвращает словарь: {by_path: {"name": "cam_1", "port": "usb-0:1.2"}}
    Если файл не найден — возвращает None.
    """
    if not CONFIG_FILE.exists():
        return None

    try:
        with open(CONFIG_FILE, "r") as f:
            content = f.read().strip()
            if content:
                return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        print(f"⚠️  Файл {CONFIG_FILE.name} повреждён")

    return None


def init_config():
    """
    Сканирует камеры и создаёт/обновляет cameras.json.
    Вызывается по флагу --init.
    """
    print("🔍 Сканирование USB-камер...")
    cameras = discover_cameras()

    if not cameras:
        print("❌ Камеры не найдены!")
        print("   Проверьте подключение камер и права доступа к /dev/video*")
        return False

    # Загружаем существующий конфиг (если есть)
    existing_config = load_camera_config() or {}

    # Строим новый конфиг, сохраняя старые имена
    new_config = {}
    for idx, cam in enumerate(cameras, start=1):
        if cam["by_path"] in existing_config:
            # Сохраняем старое имя
            new_config[cam["by_path"]] = existing_config[cam["by_path"]]
        else:
            # Новая камера
            new_config[cam["by_path"]] = {
                "name": f"cam_{idx}",
                "port": cam["port"],
            }

    save_config(new_config)

    # Выводим результат
    print(f"\n📷 Найдено {len(cameras)} камер(а):")
    for cam in cameras:
        cam_cfg = new_config.get(cam["by_path"], {})
        name = cam_cfg.get("name", "???")
        print(f"   • {cam['port']} → {cam['device']} [{name}]")

    print(f"\n✅ Конфигурация сохранена в {CONFIG_FILE.name}")
    print(f"   Отредактируйте файл чтобы задать понятные имена камер")
    return True