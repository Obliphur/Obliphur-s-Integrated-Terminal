"""演奏音频播放器；单路播放、可取消，并持续复核真实计时状态。"""

import ctypes
from pathlib import Path
import threading
import time
import uuid

from i18n import tr


class MusicPlayback:
    def __init__(self, allowed):
        self.allowed = allowed
        self.lock = threading.RLock()
        self.device_lock = threading.Lock()
        self.alias = "oit_music_" + uuid.uuid4().hex
        self.token = 0
        self.error = ""
        self.opened = False
        self.playing = False

    @property
    def is_playing(self):
        with self.lock:
            return self.playing

    def _mci(self, command):
        buf = ctypes.create_unicode_buffer(256)
        if ctypes.windll.winmm.mciSendStringW(command, buf, len(buf), None):
            raise OSError("MCI")
        return buf.value

    def stop(self):
        """通知拥有 MCI 设备的播放线程关闭；跨线程直接 close 会找不到别名。"""
        with self.lock:
            self.token += 1
            self.playing = False

    def play(self, path):
        """停止上一首；加载结束和播放期间均检查是否仍允许播放。"""
        self.stop()
        with self.lock:
            token = self.token
            self.error = ""
            if not self.allowed():
                return False

        def run():
            # MCI 别名归创建线程所有。新曲等待上一首在原线程关闭，避免叠播和设备泄漏。
            with self.device_lock:
                opened = False
                try:
                    if token != self.token or not self.allowed():
                        return
                    audio = Path(path).resolve()
                    if not audio.is_file():
                        raise OSError("missing audio")
                    self._mci(f'open "{audio}" type mpegvideo alias {self.alias}')
                    opened = self.opened = True
                    if token != self.token or not self.allowed():
                        return
                    self._mci("play " + self.alias)
                    with self.lock:
                        self.playing = True
                    while True:
                        time.sleep(0.1)
                        if token != self.token or not self.allowed():
                            return
                        if self._mci("status " + self.alias + " mode") != "playing":
                            return
                except Exception:
                    with self.lock:
                        if token == self.token:
                            self.error = tr("演奏播放失败.")
                finally:
                    if opened:
                        try:
                            self._mci("close " + self.alias)
                        except (OSError, AttributeError):
                            pass
                        self.opened = False
                    with self.lock:
                        self.playing = False

        threading.Thread(target=run, name="oit-music-playback", daemon=True).start()
        return True
