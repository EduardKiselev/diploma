#!/usr/bin/env python3
"""
UI-приложение для захвата фото и видео с USB-камер.

Оператор вводит номер телеги (1-999), нажимает «Начать обработку».
Делаются фото со всех камер, затем с первой пишется видео 5 минут.
Видео копируется на сервер (заглушка).

Использование:
    python capture_new.py           — запустить UI
    python capture_new.py --init    — сканировать камеры и создать конфиг
"""

import cv2
import sys
import time
import signal
import logging
import paramiko
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QGridLayout, QLabel, QPushButton, QLineEdit,
                               QProgressBar, QMessageBox)

from camera_config import discover_cameras, load_camera_config, init_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
PHOTOS_DIR = SCRIPT_DIR / "photos"
VIDEOS_DIR = SCRIPT_DIR / "videos"
WARMUP_FRAMES = 10
CAPTURE_DELAY = 2

VIDEO_DURATION_SEC = 2 * 60
VIDEO_FPS = 10
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

PHOTOS_DIR.mkdir(exist_ok=True)
VIDEOS_DIR.mkdir(exist_ok=True)

# SSH сервер для копирования файлов
SSH_HOST = "192.168.1.100"
SSH_USER = "kiseleveduard"
SSH_REMOTE_DIR = "~/kiln_cameras"


# ─── Поток записи видео ─────────────────────────────────────────────────────

class CaptureThread(QThread):
    """Запись видео с первой камеры в отдельном потоке."""
    progress = Signal(int)
    finished_ok = Signal(str)
    error = Signal(str)

    def __init__(self, device_path, camera_name, cart_number):
        super().__init__()
        self.device_path = device_path
        self.camera_name = camera_name
        self.cart_number = cart_number
        self._running = True

    def run(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.cart_number}_{timestamp}.mp4"
        filepath = VIDEOS_DIR / filename

        cap = cv2.VideoCapture(self.device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            self.error.emit(f"Не удалось открыть {self.device_path}")
            return

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        for _ in range(WARMUP_FRAMES):
            cap.read()

        writer = cv2.VideoWriter(str(filepath), fourcc, VIDEO_FPS,
                                 (VIDEO_WIDTH, VIDEO_HEIGHT))
        if not writer.isOpened():
            cap.release()
            self.error.emit("Не удалось создать видеофайл")
            return

        start_time = time.time()
        frame_interval = 1.0 / VIDEO_FPS
        next_frame_at = start_time

        while self._running:
            elapsed = time.time() - start_time
            if elapsed >= VIDEO_DURATION_SEC:
                break

            now = time.time()
            if now >= next_frame_at:
                ret, frame = cap.read()
                if ret and frame is not None and frame.size > 0:
                    if frame.shape[1] != VIDEO_WIDTH or frame.shape[0] != VIDEO_HEIGHT:
                        frame = cv2.resize(frame, (VIDEO_WIDTH, VIDEO_HEIGHT))
                    writer.write(frame)
                next_frame_at = now + frame_interval

            self.progress.emit(int(elapsed))
            self.msleep(50)

        writer.release()
        cap.release()

        logger.info(f"🎥 Видео сохранено: {filepath} ({filepath.stat().st_size / 1024 / 1024:.1f} МБ)")
        self.finished_ok.emit(str(filepath))

    def stop(self):
        self._running = False


# ─── Поток захвата фото ─────────────────────────────────────────────────────

class PhotosThread(QThread):
    """Захват фото со всех камер в отдельном потоке."""
    photo_done = Signal(str, int, int)   # имя файла, номер, всего
    photo_fail = Signal(str, int, int)   # имя камеры, номер, всего
    all_done = Signal(int, int)          # успех, провал

    def __init__(self, cameras, config, cart_number):
        super().__init__()
        self.cameras = cameras
        self.config = config
        self.cart_number = cart_number

    def run(self):
        success = 0
        fail = 0
        total = len(self.cameras)

        for idx, cam in enumerate(self.cameras):
            cam_cfg = self.config.get(cam["by_path"], {})
            name = cam_cfg.get("name", "unknown")

            filepath = self._capture_one(cam["device"], name)
            if filepath:
                success += 1
                self.photo_done.emit(filepath, idx + 1, total)
            else:
                fail += 1
                self.photo_fail.emit(name, idx + 1, total)

            if idx < total - 1:
                time.sleep(CAPTURE_DELAY)

        self.all_done.emit(success, fail)

    def _capture_one(self, device_path, camera_name):
        cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            return None

        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        for _ in range(WARMUP_FRAMES):
            cap.read()

        frame = None
        for _ in range(3):
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                break
            time.sleep(0.1)

        cap.release()

        if frame is None or frame.size == 0:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.cart_number}_{timestamp}_{camera_name}.jpg"
        filepath = PHOTOS_DIR / filename
        cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return str(filepath)


# ─── Основной UI ─────────────────────────────────────────────────────────────

class CaptureUI(QWidget):
    def __init__(self):
        super().__init__()
        self.cameras = []
        self.config = {}
        self.photos_thread = None
        self.capture_thread = None
        self.cart_number = 1

        self.init_ui()
        self.load_cameras()

    # ── Интерфейс ──

    def init_ui(self):
        self.setStyleSheet("background-color: #1e1e2e; color: #ffffff;")
        self.setWindowTitle("Захват камер")

        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Номер телеги — в одну строку
        cart_row = QHBoxLayout()
        cart_label = QLabel("Номер телеги:")
        cart_label.setStyleSheet("font-size: 28px;")
        cart_row.addWidget(cart_label)

        self.cart_input = QLineEdit()
        self.cart_input.setReadOnly(True)
        self.cart_input.setAlignment(Qt.AlignCenter)
        self.cart_input.setPlaceholderText("...")
        self.cart_input.setFixedHeight(70)
        self.cart_input.setStyleSheet("""
            QLineEdit {
                font-size: 38px;
                font-weight: bold;
                background-color: #313244;
                color: #ffffff;
                border: 3px solid #89dceb;
                border-radius: 10px;
                padding: 5px;
            }
        """)
        cart_row.addWidget(self.cart_input)
        main_layout.addLayout(cart_row)

        # Цифровая клавиатура — 2 ряда
        numpad = QGridLayout()
        numpad.setSpacing(10)

        btn_style = """
            QPushButton {
                font-size: 32px;
                font-weight: bold;
                background-color: #45475a;
                color: #ffffff;
                border-radius: 12px;
                min-height: 100px;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
        """
        del_style = """
            QPushButton {
                font-size: 24px;
                font-weight: bold;
                background-color: #f38ba8;
                color: #1e1e2e;
                border-radius: 12px;
                min-height: 100px;
            }
            QPushButton:pressed {
                background-color: #eba0ac;
            }
        """
        enter_style = """
            QPushButton {
                font-size: 24px;
                font-weight: bold;
                background-color: #a6e3a1;
                color: #1e1e2e;
                border-radius: 12px;
                min-height: 100px;
            }
            QPushButton:pressed {
                background-color: #94e2d5;
            }
        """

        # Ряд 1: 1 2 3 4 5 ⌫
        for i, digit in enumerate("12345"):
            btn = QPushButton(digit)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda checked, d=digit: self.on_digit(d))
            numpad.addWidget(btn, 0, i)

        btn_del = QPushButton("⌫")
        btn_del.setStyleSheet(del_style)
        btn_del.clicked.connect(self.on_backspace)
        numpad.addWidget(btn_del, 0, 5)

        # Ряд 2: 6 7 8 9 0 Ввод
        for i, digit in enumerate("67890"):
            btn = QPushButton(digit)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda checked, d=digit: self.on_digit(d))
            numpad.addWidget(btn, 1, i)

        btn_enter = QPushButton("Ввод ✓")
        btn_enter.setStyleSheet(enter_style)
        btn_enter.clicked.connect(self.on_enter)
        numpad.addWidget(btn_enter, 1, 5)

        main_layout.addLayout(numpad)

        # Статус
        self.status_label = QLabel("Готов к работе")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 26px; color: #a6e3a1;")
        main_layout.addWidget(self.status_label)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, VIDEO_DURATION_SEC)
        self.progress_bar.setFixedHeight(35)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #313244;
                border-radius: 10px;
                text-align: center;
                font-size: 18px;
                color: #ffffff;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #89dceb;
                border-radius: 10px;
            }
        """)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Таймер обратного отсчёта
        self.timer_label = QLabel("")
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setStyleSheet("font-size: 24px; color: #f9e2af;")
        self.timer_label.setVisible(False)
        main_layout.addWidget(self.timer_label)

        self.setLayout(main_layout)
        self.showFullScreen()

    def load_cameras(self):
        self.config = load_camera_config()
        if self.config is None:
            self.set_numpad_enabled(False)
            return

        self.cameras = discover_cameras()
        if not self.cameras:
            self.set_numpad_enabled(False)
            return

        logger.info(f"📷 Найдено {len(self.cameras)} камер(а)")

    # ── Запуск обработки ──

    def start_capture(self):
        text = self.cart_input.text().strip()
        if not text:
            self.show_message("Ошибка", "Введите номер телеги")
            return
        self.cart_number = int(text)
        self.set_numpad_enabled(False)

        self.set_status("📸 Делаю фото со всех камер...", "#f9e2af")
        QApplication.processEvents()

        # Запуск потока фото
        self.photos_thread = PhotosThread(self.cameras, self.config, self.cart_number)
        self.photos_thread.photo_done.connect(self.on_photo_done)
        self.photos_thread.photo_fail.connect(self.on_photo_fail)
        self.photos_thread.all_done.connect(self.on_photos_complete)
        self.photos_thread.start()

    def on_photo_done(self, filepath, num, total):
        name = Path(filepath).name
        self.set_status(f"📸 [{num}/{total}] {name} ✓", "#a6e3a1")
        logger.info(f"✅ Фото: {name}")
        self.upload_to_server(filepath)

    def on_photo_fail(self, camera_name, num, total):
        self.set_status(f"❌ [{num}/{total}] Ошибка: {camera_name}", "#f38ba8")
        logger.warning(f"❌ Ошибка фото с {camera_name}")

    def on_photos_complete(self, success, fail):
        logger.info(f"📸 Фото готово: {success} успешно, {fail} ошибок")

        if not self.cameras:
            self.set_status("❌ Нет камер для видео", "#f38ba8")
            self.reset_ui()
            return

        # Переход к записи видео с первой камеры
        first_cam = self.cameras[0]
        first_cfg = self.config.get(first_cam["by_path"], {})
        first_name = first_cfg.get("name", "cam_1")

        self.set_status(f"🎥 Запись видео с {first_name}...", "#f38ba8")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.timer_label.setVisible(True)
        self.timer_label.setText(f"Осталось: 5:00")
        QApplication.processEvents()

        self.capture_thread = CaptureThread(
            first_cam["device"], first_name, self.cart_number
        )
        self.capture_thread.progress.connect(self.on_video_progress)
        self.capture_thread.finished_ok.connect(self.on_video_finished)
        self.capture_thread.error.connect(self.on_video_error)
        self.capture_thread.start()

    # ── Видео прогресс / завершение ──

    def on_video_progress(self, elapsed_sec):
        self.progress_bar.setValue(elapsed_sec)
        remaining = VIDEO_DURATION_SEC - elapsed_sec
        minutes = remaining // 60
        seconds = remaining % 60
        self.timer_label.setText(f"Осталось: {minutes}:{seconds:02d}")

    def on_video_finished(self, filepath):
        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)

        self.upload_to_server(filepath)

        self.set_status("✅ Обработка завершена!", "#a6e3a1")
        self.reset_ui()

    def on_video_error(self, message):
        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)
        self.set_status("❌ Ошибка записи видео", "#f38ba8")
        self.reset_ui()
        self.show_message("Ошибка видео", message)

    # ─── Копирование на сервер по SSH ────────────────────────────────────────

    def upload_to_server(self, filepath):
        """Копирует файл на сервер по SSH/SFTP."""
        filepath = Path(filepath)
        # Определяем подпапку по расширению
        if filepath.suffix.lower() in (".jpg", ".jpeg", ".png"):
            remote_subdir = "photo"
        else:
            remote_subdir = "video"

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(SSH_HOST, username=SSH_USER, timeout=10)

            # Создаём директории на сервере
            remote_path = f"{SSH_REMOTE_DIR}/{remote_subdir}"
            ssh.exec_command(f"mkdir -p {remote_path}")

            # Копируем файл через SFTP
            sftp = ssh.open_sftp()
            remote_file = f"{remote_path}/{filepath.name}"
            sftp.put(str(filepath), remote_file)
            sftp.close()
            ssh.close()

            # Удаляем локальный файл после успешного копирования
            filepath.unlink()
            logger.info(f"📤 Файл скопирован на сервер: {SSH_HOST}:{remote_file}")
        except Exception as e:
            logger.error(f"❌ Ошибка копирования на сервер: {e}")

    # ── Клавиатура ──

    def on_digit(self, digit):
        current = self.cart_input.text()
        if len(current) < 3:
            self.cart_input.setText(current + digit)

    def on_backspace(self):
        current = self.cart_input.text()
        self.cart_input.setText(current[:-1])

    def on_enter(self):
        text = self.cart_input.text().strip()
        if not text:
            self.show_message("Ошибка", "Введите номер телеги")
            return
        num = int(text)
        if num < 1 or num > 999:
            self.show_message("Ошибка", "Номер от 1 до 999")
            return
        self.start_capture()

    def set_numpad_enabled(self, enabled):
        for btn in self.findChildren(QPushButton):
            text = btn.text()
            if text in "0123456789" or "Удалить" in text or "Ввод" in text:
                btn.setEnabled(enabled)

    # ── Утилиты ──

    def set_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 32px; color: {color};")

    def reset_ui(self):
        self.set_numpad_enabled(True)
        self.cart_input.clear()

    def show_message(self, title, message):
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setStyleSheet("""
            QMessageBox {
                background-color: #313244;
                color: #ffffff;
            }
            QMessageBox QLabel {
                color: #ffffff;
                font-size: 18px;
                min-width: 300px;
                padding: 15px;
            }
            QPushButton {
                font-size: 16px;
                padding: 10px 25px;
                background-color: #45475a;
                color: white;
                border-radius: 8px;
                margin: 5px;
            }
            QPushButton:hover {
                background-color: #585b70;
            }
        """)
        msg.exec()


# ─── Точка входа ─────────────────────────────────────────────────────────────

def main():
    # Обработка Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        init_config()
        return

    app = QApplication(sys.argv)
    ui = CaptureUI()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
