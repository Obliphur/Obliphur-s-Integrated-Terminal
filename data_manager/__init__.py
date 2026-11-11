

import os
from datetime import datetime
from typing import Optional, Dict

import config
import closedtest
from config import (
    APP_NAME, OLD_APP_DIR, HOUR_LOG_ENABLED
)

from .data_common import (
    month_prefixes,
    split_items,
    VIOLATION_KEEP,
    USERNAME_MAX,
    half_fill,
    _sum_days,
    _merge_spans,
    _subtract,
    combine_window,
    tally_items,
)
from .data_auto import AutoMixin
from .data_study import StudyMixin
from .data_focus import FocusMixin
from .data_overlap import OverlapMixin
from .data_window import WindowMixin
from .data_hours import HoursMixin
from .data_misc import MiscMixin


class DataManager(AutoMixin, StudyMixin, FocusMixin, OverlapMixin, WindowMixin, HoursMixin, MiscMixin):
    def __init__(self, hour_auto_text=None):
        low_dir = os.path.join(
            os.environ.get('USERPROFILE', os.path.expanduser('~')),
            'AppData', 'LocalLow'
        )
        if closedtest.active():            # 封闭测试 (MECHANICS 3.6): 全部放在项目根目录里
            low_dir = closedtest.low_dir()
        self.low_dir = low_dir
        self.data_dir = os.path.join(low_dir, APP_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        self.old_dir = os.path.join(low_dir, OLD_APP_DIR)
        # 行为模式按小时变化的自动填充内容 (MECHANICS 21); 要在 finalize_hours 之前接上
        self.hour_auto_text = hour_auto_text or (lambda hour: "")
        self.sleep_live = lambda: None       # 正在睡眠时, 开始睡眠的时刻 (app 接上); 睡眠期间的小时自动记成"睡眠"


        # 旧版本在数据目录外记录卸载次数的文件; 现在不再记录, 重置时一并删除
        self.legacy_uninstall_file = os.path.join(low_dir, f"{APP_NAME}.uninstall.json")
        self.resetting = False

        # 用户名 (第一次打开时填写, MECHANICS 1.1): {"username": ...}
        self.profile_file = os.path.join(self.data_dir, 'profile.json')
        self.study_file = os.path.join(self.data_dir, 'study_log.json')
        self.hour_file = os.path.join(self.data_dir, 'hour_log.json')
        # 计时器的每一段专注 / 休息 (起止时刻), Statistics 页画每天的时间分布用; 只留最近 7 天
        self.focus_file = os.path.join(self.data_dir, 'focus_log.json')
        self.focus_sessions = []
        # 窗口使用时间: {日期: {程序名: 秒}}, 程序名 "" 是空白; 只留本月和上月
        self.window_file = os.path.join(self.data_dir, 'window_usage.json')
        self.window_days: Dict[str, Dict[str, float]] = {}
        # 日常 (MECHANICS 19): 睡觉时间的提示 / 合约开始时的睡眠提醒已经弹过的日期
        self.daily_file = os.path.join(self.data_dir, 'daily.json')
        self.daily: Dict[str, str] = {}
        # 违规记录 (MECHANICS 8.1): 最近 VIOLATION_KEEP 条合约违规的提示文字, 新的在后
        self.violations_file = os.path.join(self.data_dir, 'violations.json')
        self.violations: list = []
        # 同一份数据按分钟的时间线: {日期: [[开始分钟, 结束分钟, 程序名], …]};
        # 统计页看"全部设备"时用它判断几台设备是不是同时在用 (重叠的分钟以母端为准)
        self.window_runs: Dict[str, list] = {}
        self._window_dirty = False

        self.study_data: Dict[str, float] = {}
        # 行为模式的外出记录 (MECHANICS 21): 不写进 study_log / focus_log, 显示时叠加.
        # app 启动时接上 behavior.Behavior 的对应方法.
        self.extra_study = lambda day: 0.0          # (日期字符串) → 当天外出的专注小时
        self.extra_days = lambda: set()             # 有外出的日期字符串
        self.extra_focus = lambda day: []           # (某一天) → [(kind, 开始, 结束)]
        # 设备同步: 其他设备的账 (devsync.SyncService.peer_ledgers), 显示合计用
        self.peer_ledgers = lambda: []
        # 有设备离线的时间段 [(开始毫秒, 结束毫秒)] (devsync.offline_spans): 正在进行的那段也按它画成空白
        self.offline_spans = lambda a, b: []
        self.peer_ledgers_by_id = lambda: {}     # {设备 id: 账}
        self.my_id = lambda: ""
        self.mother_id = lambda: None            # 母端的设备 id
        self.hour_days: Dict[str, Dict[str, str]] = {}
        # 合约的细化规范 (MECHANICS 18.4): 一小时分成两个半小时各填一格 {日期: {小时: [前半, 后半]}};
        # hour_days 里那一小时是两格用 "/" 连起来的内容, 统计照常按它算.
        self.hour_halves: Dict[str, Dict[str, list]] = {}
        self.hour_cursor: Optional[datetime] = None

        self.hour_enabled = bool(getattr(config, "HOUR_LOG_ENABLED", HOUR_LOG_ENABLED))
        self.today_date = datetime.now().strftime('%Y-%m-%d')
        self.prompt_slot: Optional[datetime] = None
        self.prompt_len = 60                     # 待填写的那一格有几分钟 (细化规范: 30)

        self._migrate_old_data()
        self.load_study_data()
        self.prune_study_data()
        self.load_hour_log()
        self.finalize_hours()
        self.load_focus_log()
        self.load_window_usage()
        self.load_daily()
        self.load_violations()
