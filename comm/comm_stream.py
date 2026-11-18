# -*- coding: utf-8 -*-
"""流式正文的显示时钟；接收速度与按字显示速度相互独立。"""
from bisect import bisect_right
from math import ceil


class StreamDisplay:
    def __init__(self, delay, earliest=0):
        self.delay = delay
        self.earliest = earliest
        self.source = ""
        self.shown = 0
        self._times = []

    @property
    def text(self):
        return self.source[:self.shown]

    @property
    def due(self):
        return ceil(self._times[-1]) if self._times else self.earliest

    def update(self, text, now):
        """保留相同前缀的进度；断流后的空档不积攒瞬间显示的额度。"""
        common = 0
        for old, new in zip(self.source, text):
            if old != new:
                break
            common += 1
        del self._times[common:]
        self.shown = min(self.shown, common)
        at = max(now, self.earliest, self._times[-1] if self._times else 0)
        for char in text[common:]:
            at += max(0, self.delay(char) * 1000)
            self._times.append(at)
        self.source = text

    def advance(self, now):
        shown = bisect_right(self._times, now)
        changed = shown != self.shown
        self.shown = shown
        return changed
