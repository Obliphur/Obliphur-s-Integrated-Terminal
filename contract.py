"""
合约 (MECHANICS.md 第 18 节): 词条, 进行中的合约, 结束后的记录. 界面在 pages/contract_page.py.

存档 contract.json: 进行中的合约 (live), 结束了的合约 (history, 整点记录的锁定也看它), 没开始的草稿 (draft).
live 和 history 随设备同步 (doc / merge), draft 只留在本机.
"""

import os
import random
import re
from datetime import datetime, timedelta

import config
from config import load_json_file, save_json_file


BASE_SECONDS = 4 * 3600          # 合约的初始时长
IDLE_LIMIT = 15 * 60             # 合约中空闲 (不在专注也不在休息) 超过这么久就结束
OFFLINE_LIMIT = IDLE_LIMIT       # 合约中有绑定的设备离线超过这么久就失败 (和空闲共用这 15 分钟)
VIOLATION_LIMIT = 4              # 初始目标生命值: 每违反一次减一, 减到 0 合约失败
HISTORY_KEEP = 30
FRESH_MS = 2 * 60 * 1000         # 合约的主人这么久没同步过: 本机来做自动结束

# (id, 组, 等级, 名字, 描述, 冲突的词条)
# 同一组的词条互相冲突 (系列的各档在同一列); 冲突是双向的 (这里只写一边).
# 顺序同词条板 (BOARD): 第 1 页 合约本身 / 专注 / 休息 / 整点记录, 第 2 页 联网 / 网络禁令 / 录像 / 现实 / 黑雾 / 未知变量.
TERMS = [
    # ==== 第 1 页 ====
    # ---- 合约本身: 时长, 目标生命值 ----
    ("long1", "long", 1, "环境:持久战-I", "时长改为120%, 休息时长×110%", ()),
    ("long2", "long", 2, "环境:持久战-II", "时长改为140%, 休息时长×120%", ()),
    ("long3", "long", 3, "环境:持久战-III", "时长改为160%, 休息时长×130%", ()),
    ("retreat1", "retreat", 1, "环境:退路封锁-I", "初始目标生命值为3", ()),
    ("retreat2", "retreat", 2, "环境:退路封锁-II", "初始目标生命值为2", ()),
    ("last", "retreat", 3, "最后防线", "初始目标生命值为1", ()),
    # ---- 专注: 专注的下限, 专注中的窗口数 ----
    ("deep1", "deep", 1, "目标:纵深推进-I", "专注的时间不能少于40min", ()),
    ("deep2", "deep", 2, "目标:纵深推进-II", "专注的时间不能少于80min", ()),
    ("chan1", "chan", 1, "环境:通道阻塞-I", "在专注期间的最大窗口数改为4", ()),
    ("chan2", "chan", 2, "环境:通道阻塞-II", "在专注期间的最大窗口数改为3", ()),
    # ---- 休息: 休息时间的倍率, 合约开始前积攒的休息时间 ----
    ("tired1", "tired", 1, "环境:疲惫-I", "基础休息倍率由80%改为70%", ()),
    ("tired2", "tired", 2, "环境:疲惫-II", "基础休息倍率由80%改为60%", ()),
    ("tired3", "tired", 3, "环境:疲惫-III", "基础休息倍率由80%改为50%", ()),
    ("short1", "short", 1, "环境:支援短缺-I", "可用的合约开始前已保存休息时间改为50%", ()),
    ("short2", "short", 2, "环境:支援短缺-II", "不能使用合约开始前已经保存的休息时间", ()),
    ("hoard", "hoard", 1, "禁止囤囤鼠!", "不能提前结束休息 但是获得的休息时间改为110%", ("short1", "short2")),
    # ---- 整点记录 ----
    ("seal", "seal", 1, "环境:记录封锁", "无法修改已经被填写的格子 (包括自动填写和手动填写); 每次完成填写时, 另外随机封锁两个可以封锁的格子", ()),
    ("fine", "fine", 2, "目标:细化规范", "记录变为30min一次", ()),
    # ==== 第 2 页 ====
    # ---- 联网: 剩余额度, 冷静期, 快速访问, 断网方式, 专注中的联网 ----
    ("info1", "info", 1, "环境:信息阻断-I", "可用的剩余联网时间改为80% 且不能使用借贷", ()),
    ("info2", "info", 2, "环境:信息阻断-II", "可用的剩余联网时间改为65% 且不能使用借贷", ()),
    ("info3", "info", 3, "环境:信息阻断-III", "可用的剩余联网时间改为50% 且不能使用借贷", ()),
    ("erode1", "erode", 1, "环境:侵蚀-I", "网络申请冷却期改为200%", ()),
    ("erode2", "erode", 2, "环境:侵蚀-II", "网络申请冷却期改为300%", ()),
    ("suppress", "suppress", 1, "环境:抑制", "不能使用快速访问功能", ()),
    ("jam", "jam", 2, "环境:全频道阻塞干扰", "期间使用proxy block-all模式", ()),
    ("quiet", "quiet", 1, "环境:静默协议", "专注中不能处于联网状态", ()),
    # ---- 网络禁令: 这一类软件 / 网站禁止联网 ----
    ("music", "music", 1, "环境:乐理之灾", "合约期间音乐播放软件禁止联网 可以通过点击解除网络禁令", ()),
    ("silent", "silent", 2, "环境:无言为真", "合约期间社交软件和与AI聊天的软件禁止联网 可以通过点击解除网络禁令", ()),
    ("game", "game", 1, "环境:禁游令", "合约期间游戏和游戏平台禁止联网 可以通过点击解除网络禁令", ()),
    ("video", "video", 1, "环境:低帧的诅咒", "合约期间视频平台禁止联网 可以通过点击解除网络禁令", ()),
    # ---- 录像 (目光 / 刻痕), 现实 (高机动 / 禁足: 前置条件是目光) ----
    ("gaze", "gaze", 1, "目光", "合约期间用电脑或手机的摄像头记录使用者的行为 结束后保存录像", ()),
    ("scar", "scar", 1, "刻痕", "合约期间录制整个屏幕 结束后保存录像", ()),
    ("move", "move", 1, "目标:高机动", "现实中的移动速度+50%", ()),
    ("confine", "confine", 1, "目标:禁足", "在非休息时间不能离开工作岗位进行活动", ()),
    # ---- 黑雾, 未知变量 ----
    ("fog", "fog", 1, "环境:黑雾", "合约期间 专注时长 合约的剩余时间 成就 日历 统计数据不可见 且无法打开设置", ()),
    ("unknown", "unknown", 3, "未知变量", "合约开始时 以及之后每过一个整小时 从当前可以选择的1-2级词条中随机启用一个或随机施加效果\n选中的词条不算进危机等级", ()),
]
BY_ID = {t[0]: t for t in TERMS}
GROUPS = list(dict.fromkeys(t[1] for t in TERMS))
# 前置条件: 选了后面的词条才能选这个 (MECHANICS 18.3); 去掉前置的词条时它也一起去掉. 合约中前置失效了不影响.
NEEDS = {"move": "gaze", "confine": "gaze"}
# 词条板 (MECHANICS 18.3): 每一列从上往下按等级 (1 → 3), 同一类的放在一列, 相似的挨着; 两页各 18 个词条.
BOARD = [
    # 第 1 页 (0-9): 合约本身, 专注, 休息, 整点记录, 联网 (额度 / 冷静期)
    ("long1", "long2", "long3"), ("retreat1", "retreat2", "last"), ("deep1", "deep2"), ("chan1", "chan2"),
    ("tired1", "tired2", "tired3"), ("short1", "short2"), ("hoard",), ("seal", "fine"),
    ("info1", "info2", "info3"), ("erode1", "erode2"),
    # 第 2 页 (10-19): 联网 (快速访问 / 静默), 网络禁令, 录像, 现实, 黑雾 / 未知变量
    ("suppress", "jam"), ("quiet",), ("music", "silent"), ("game",), ("video",),
    ("gaze",), ("scar",), ("move",), ("confine",), ("fog", "unknown"),
]
PAGE_SPLIT = 10                                         # 第 1 页的列数 (两页各 10 列, 均匀分布)
LAYOUT = {t: (i, BY_ID[t][2]) for i, col in enumerate(BOARD) for t in col}   # 词条 → (列, 等级)
BOARD_COLS = len(BOARD)
MAX_LEVEL = max(t[2] for t in TERMS)
# 词条板翻页 (每页的 [开始列, 结束列)); 窗口放不下一页时界面再往下分.
# 第 3 页 (COMBO_PAGE) 是词条组合的效果 (COMBOS), 由界面直接画, 不分列.
PAGES = [(0, PAGE_SPLIT), (PAGE_SPLIT, BOARD_COLS)]
COMBO_PAGE = len(PAGES)

# 软规则: 界面上照样能做, 但会被算作违反, 这个词条降一级 (结算按降级后的等级);
# 其余是硬规则, 立即生效 (黑雾, 记录封锁, 细化规范, 全频道阻塞干扰, 持久战, 疲惫).
# 录像 (目光 / 刻痕) 违反一次就失效 (录像停止): 不在 LOWER / SINGLE 里.
SOFT = {"info1", "info2", "info3", "chan1", "chan2", "music", "suppress", "short1", "short2",
        "silent", "game", "video", "gaze", "scar", "deep1", "deep2", "quiet", "hoard", "unknown"}
LOWER = {"info3": "info2", "info2": "info1", "chan2": "chan1",
         "short2": "short1", "deep2": "deep1"}   # 降一级换成哪个词条
SINGLE = {"music", "suppress", "silent", "game", "video", "quiet", "hoard"}   # 只有一档的软规则: 降级时等级减一, 减到 0 就失效

DURATION = {"long1": 1.2, "long2": 1.4, "long3": 1.6}
NET_SHARE = {"info1": 0.8, "info2": 0.65, "info3": 0.5}
BASE_REST = 0.8                                # 合约中专注换来的休息时间的基础倍率; 有疲惫时换成疲惫的倍率
REST = {"tired1": 0.7, "tired2": 0.6, "tired3": 0.5}
REST_BONUS = {"hoard": 1.1, "long1": 1.1, "long2": 1.2, "long3": 1.3}   # 休息时间额外的乘区 (相乘): 禁止囤囤鼠! ×1.1; 持久战 ×110/120/130%
WINDOWS = {"chan1": 4, "chan2": 3}
SUPPLY = {"short1": 0.5, "short2": 0.0}         # 合约开始前积攒的休息时间能用几成
HOUR_TERMS = {"seal", "fine"}                   # 整点记录的词条: 整点记录关着时, 这次合约中照样开启
LIFE = {"retreat1": 3, "retreat2": 2, "last": 1}  # 初始目标生命值 (硬规则)
RECORD = {"gaze": "camera", "scar": "screen"}   # 录像的词条 → 录什么 (recorder.py)
# 网络禁令 (MECHANICS 18.3): 这一类软件合约期间禁止联网 (打开它们不算违反); 点击词条可以解除, 扣掉词条等级那么多目标生命值
BANS = ("music", "silent", "game", "video")
SUPPLY_WARN = (180, 60)                         # 支援短缺: 还差这么多秒就用超时各提醒一次
DEEP = {"deep1": 40 * 60, "deep2": 80 * 60}     # 纵深推进: 一段专注至少这么多秒, 不到就结束算违反
SEAL_EXTRA = 2                                  # 记录封锁: 每次完成填写时另外随机封锁几个格子
COOLDOWN_X = {"erode1": 2.0, "erode2": 3.0}     # 侵蚀: 网络申请的冷静期乘多少 (硬规则)
RANDOM = "unknown"                              # 未知变量: 合约的每个小时开始时随机启用一个 1-2 级的词条, 那个小时结束时移除
RANDOM_LEVELS = (1, 2)
RANDOM_SKIP = {"gaze", "scar", "long1", "long2"}  # 不会选中录像和持久战的词条
RANDOM_DELAY = timedelta(minutes=3)             # 抽中后过这么久才生效
# 未知变量的每次判定: 有能选的词条时 75% 抽词条, 25% 施加效果; 没有能选的词条时一定是效果.
# 不选已经选过的词条; 通过违反失效的词条 (开始时选的, 或者未知变量选中的) 可以再回到词条池, 但只会再被选中一次.
RANDOM_TERM_P = 0.75
RANDOM_EFFECTS = (("rest_cut", 15), ("lock", 15), ("rest_add", 15), ("double", 15), ("slow", 10), ("none", 30))
RANDOM_REST = 10 * 60                           # 休息时间 -10 / +10 分钟 (正在休息时改这次休息, 否则改积攒的)
RANDOM_SLOW = 0.9                               # 阻滞: 下一次专注的时间按 90% 增长
RANDOM_ROLLS = 9                                # 一个小时里最多判定这么多次 ("再判定两次"连着抽到时)
# 被违反到失效的词条 (降到最低一档后又违反了) 照样检查: 再次触发只响警报, 不扣目标生命值; 每个词条 2 分钟最多响一次
ECHO_COOLDOWN = 120
# 未知变量抽中的词条 3 分钟后生效, 只在抽中的那个小时里有效, 这个小时结束时移除 (不算违反); 同时只有一个.
# 退路封锁只在目标生命值减少后不为 0 时才选

# 总等级 → 完成合约时给的休息时间 (分钟, 固定; 按结束时的等级)
# 0 级 20 分钟, 38 级起封顶 90 分钟; 中间每一级按直线填充, 四舍五入到整分钟
REST_MIN, REST_MAX, REST_TOP = 20, 60, 38

# 乐理之灾: 常见的音乐播放软件 (exe 名, 小写) 和音频文件扩展名 (窗口标题里出现就算在放).
# 视频播放器 (PotPlayer, VLC, MPC, KMPlayer, Windows 媒体播放器 …) 不在名单里: 只有用它们打开音频文件 (看标题) 才算.
MUSIC_EXES = {
    "cloudmusic.exe", "qqmusic.exe", "kugou.exe", "kwmusic.exe", "kuwomusic.exe", "spotify.exe",
    "foobar2000.exe", "aimp.exe", "itunes.exe", "applemusic.exe", "music.ui.exe",
    "musicbee.exe", "winamp.exe", "mediamonkey.exe",
    "dopamine.exe", "lx-music-desktop.exe", "yesplaymusic.exe", "listen1.exe", "tidal.exe",
    "deezer.exe", "amazon music.exe", "qianqian.exe", "baidumusic.exe", "migu music.exe",
    "musicplayer2.exe", "groove.exe",
}
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".ape", ".opus", ".ncm", ".kgm")

# 无言为真: 常见的社交软件和与 AI 聊天的软件 (exe 名, 小写; 进程在就算, 缩到托盘里也算).
# 社交网站 / 网页版的 AI: 浏览器窗口的标题里有这些名字 (小写比较; 搜索结果页不算).
SOCIAL_EXES = {
    "wechat.exe", "weixin.exe", "qq.exe", "tim.exe", "qqnt.exe", "telegram.exe", "discord.exe",
    "whatsapp.exe", "line.exe", "skype.exe", "signal.exe", "messenger.exe", "kakaotalk.exe", "weibo.exe",
    "xiaohongshu.exe",
    "chatgpt.exe", "claude.exe", "doubao.exe", "kimi.exe", "yuanbao.exe", "tongyi.exe", "qwen.exe",
    "deepseek.exe", "copilot.exe", "m365copilot.exe", "chatbox.exe", "cherry studio.exe", "poe.exe",
    "perplexity.exe", "wenxiaoyan.exe",
}
SOCIAL_TITLES = {
    "微博", "weibo", "知乎", "小红书", "百度贴吧", "贴吧", "豆瓣", "qq空间", "微信", "微信网页版",
    "twitter", "x", "facebook", "instagram", "reddit", "discord", "telegram", "telegram web", "whatsapp",
    "threads", "chatgpt", "claude", "deepseek", "gemini", "copilot", "microsoft copilot", "豆包", "kimi", "通义",
    "通义千问", "文心一言", "腾讯元宝", "元宝", "perplexity", "grok", "poe",
    "claude.ai", "chatgpt.com", "kimi.ai", "chat.deepseek.com", "x.com", "weibo.com", "zhihu.com",
}
SEARCH_TITLES = ("搜索", "search")


def site_title(title: str, names) -> bool:
    """浏览器窗口的标题是不是这些网站 (names: 小写的网站名 / 域名) 之一: 标题按 - | _ / 等切开, 有一段就是网站的名字
    (或者是 "名字 " 后面跟着不含英文字母的说明, 如 "Kimi 智能助手"). 搜索结果页不算 ("知乎 - Google 搜索")."""
    t = str(title or "").lower()
    if not t or any(k in t for k in SEARCH_TITLES):
        return False
    for seg in re.split(r"\s[-|_–—/·:]\s|[|_–—·]|(?<=[一-鿿])-|-(?=[一-鿿])", t):
        seg = seg.strip().lstrip("(0123456789) ").strip()
        if seg in names:
            return True
        head, _sp, rest = seg.partition(" ")
        if head in names and len(head) > 1 and not re.search(r"[a-z]", rest):
            return True
    return False


# 低帧的诅咒: 常见的视频平台 (客户端的 exe 名, 网页的网站名). 本地的视频播放器 (PotPlayer, VLC …) 不算.
VIDEO_EXES = {
    "bilibili.exe", "哔哩哔哩.exe", "youku.exe", "youkuclient.exe", "qqlive.exe", "tencentvideo.exe",
    "qyclient.exe", "iqiyi.exe", "mgtv.exe", "imgotv.exe", "xigua.exe", "xiguavideo.exe", "douyin.exe",
    "kuaishou.exe", "douyu.exe", "huyaclient.exe", "huya.exe", "twitch.exe", "netflix.exe", "acfun.exe",
}
VIDEO_TITLES = {
    "哔哩哔哩", "bilibili", "youtube", "优酷", "腾讯视频", "爱奇艺", "芒果tv", "西瓜视频", "抖音", "快手", "tiktok",
    "twitch", "netflix", "斗鱼", "虎牙", "虎牙直播", "斗鱼直播", "acfun", "niconico", "好看视频", "微信视频号",
    "bilibili.com", "youtube.com", "douyin.com",
}
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
    "360se.exe", "360chrome.exe", "qqbrowser.exe", "sogouexplorer.exe", "2345explorer.exe", "liebao.exe",
    "quark.exe", "iexplore.exe", "chromium.exe", "yandex.exe", "waterfox.exe", "librewolf.exe", "floorp.exe",
}
# 禁游令: 常见的游戏平台和游戏 (exe 名, 小写; 进程在就算)
GAME_EXES = {
    "steam.exe", "epicgameslauncher.exe", "wegame.exe", "tgp_daemon.exe", "eadesktop.exe", "origin.exe",
    "battle.net.exe", "upc.exe", "ubisoftconnect.exe", "galaxyclient.exe", "riotclientservices.exe",
    "riotclientux.exe", "hyp.exe", "launcher_epic.exe", "rockstarlauncher.exe", "playnite.desktopapp.exe",
    "leagueclient.exe", "league of legends.exe", "valorant.exe", "valorant-win64-shipping.exe",
    "yuanshen.exe", "genshinimpact.exe", "starrail.exe", "zenlesszonezero.exe", "bh3.exe",
    "minecraft.exe", "minecraftlauncher.exe", "cs2.exe", "csgo.exe", "dota2.exe", "overwatch.exe",
    "tslgame.exe", "gta5.exe", "gta5_enhanced.exe", "eldenring.exe", "hearthstone.exe", "wow.exe",
    "fortniteclient-win64-shipping.exe", "r5apex.exe", "robloxplayerbeta.exe", "osu!.exe",
    "narakabladepoint.exe", "rocketleague.exe", "terraria.exe", "stardew valley.exe", "rainbowsix.exe",
    "destiny2.exe", "warframe.x64.exe", "pathofexile.exe", "pathofexile_x64.exe", "diablo iv.exe",
    "cod.exe", "blackops6.exe", "palworld-win64-shipping.exe", "helldivers2.exe", "baldur's gate 3.exe",
    "bg3.exe", "bg3_dx11.exe", "cyberpunk2077.exe", "witcher3.exe", "sekiro.exe", "hollow knight.exe",
    "deltaforceclient-win64-shipping.exe", "crossfire.exe", "dnf.exe", "4399game.exe",
}
BAN_EXES = {"silent": SOCIAL_EXES, "game": GAME_EXES, "video": VIDEO_EXES}
NET_BAN_EXES = {"music": MUSIC_EXES, "silent": SOCIAL_EXES, "game": GAME_EXES, "video": VIDEO_EXES}   # 禁止联网的程序
# 网络禁令也封锁这一类的网站 (MECHANICS 18.3): 域名本身和它的子域名; 优先级最高, 联网申请也解除不了
BAN_DOMAINS = {
    "music": (
        "music.163.com", "y.qq.com", "kugou.com", "kuwo.cn", "music.migu.cn", "qianqian.com", "ximalaya.com",
        "spotify.com", "music.apple.com", "music.youtube.com", "soundcloud.com", "deezer.com", "tidal.com",
        "music.amazon.com", "pandora.com",
    ),
    "silent": (
        "weibo.com", "weibo.cn", "zhihu.com", "xiaohongshu.com", "tieba.baidu.com", "douban.com",
        "qzone.qq.com", "wx.qq.com", "web.wechat.com", "twitter.com", "x.com", "facebook.com",
        "instagram.com", "threads.net", "reddit.com", "discord.com", "telegram.org", "t.me",
        "web.whatsapp.com", "line.me", "chatgpt.com", "chat.openai.com", "claude.ai", "chat.deepseek.com",
        "gemini.google.com", "kimi.moonshot.cn", "kimi.com", "doubao.com", "yuanbao.tencent.com",
        "tongyi.aliyun.com", "tongyi.com", "yiyan.baidu.com", "copilot.microsoft.com", "poe.com",
        "perplexity.ai", "grok.com", "character.ai",
    ),
    "game": (
        "steampowered.com", "steamcommunity.com", "epicgames.com", "wegame.com.cn", "ea.com", "battle.net",
        "blizzard.com", "ubisoft.com", "gog.com", "riotgames.com", "leagueoflegends.com", "mihoyo.com",
        "hoyoverse.com", "taptap.cn", "taptap.io", "4399.com", "7k7k.com", "roblox.com", "minecraft.net",
        "poki.com", "crazygames.com", "itch.io", "xbox.com", "playstation.com",
    ),
    "video": (
        "bilibili.com", "b23.tv", "youtube.com", "youtu.be", "youku.com", "v.qq.com", "iqiyi.com",
        "mgtv.com", "ixigua.com", "douyin.com", "kuaishou.com", "tiktok.com", "douyu.com", "huya.com",
        "twitch.tv", "netflix.com", "acfun.cn", "nicovideo.jp", "haokan.baidu.com", "weishi.qq.com",
        "channels.weixin.qq.com",
    ),
}
BAN_TITLES = {"silent": SOCIAL_TITLES, "video": VIDEO_TITLES}   # 只看浏览器的窗口

# 词条组合 (MECHANICS 18.3): 列出的词条 / 词条系列 (任意一档都算) 同时选着时生效, 效果栏的第 3 页列出它们.
# level: 额外的危机等级; rest: 休息时间的额外乘区; gift: 开始时给的休息时间 (秒); end: 完成时给的休息时间 (秒).
# 成员因违反失效 (降级不算失效) 时组合不再生效: 危机等级 / 乘区 / 结束后收益都没了, 开始时给的不收回.
COMBOS = [
    {"id": "mochi", "name": "打年糕", "color": "white",
     "reqs": ("tired", "long", "fine", "deep"),
     "level": 1, "rest": 1.2, "gift": 0, "end": 0,
     "desc": "额外 1 危机等级\n休息时间 ×120%"},
    {"id": "nofun", "name": "互联网娱乐的败退", "color": "purple",
     "reqs": ("music", "silent", "game", "video"),
     "level": 0, "rest": 1.0, "gift": 20 * 60, "end": 0,
     "desc": "合约开始时 休息 +20 分钟"},
    {"id": "flawless", "name": "无懈可击", "color": "gray",
     "reqs": ("last", "unknown", "move", "confine", "gaze", "scar"),
     "level": 0, "rest": 1.0, "gift": 20 * 60, "end": 0,
     "desc": "合约开始时 休息 +20 分钟"},
    {"id": "isolation", "name": "信息隔绝", "color": "red",
     "reqs": ("info", "chan", "erode", "suppress", "fog", "quiet", "jam"),
     "level": 1, "rest": 1.0, "gift": 0, "end": 10 * 60,
     "desc": "额外 1 危机等级\n完成合约时 休息 +10 分钟"},
]
COMBOS_BY_ID = {c["id"]: c for c in COMBOS}
COMBO_COLORS = {"white": "#ffffff", "purple": "#b06cf0", "gray": "#9a9ca1", "red": "#ff2b3a"}
# 组合的要求在界面上怎么写: 成系列的一组只写一个名字
GROUP_NAMES = {"long": "持久战", "tired": "疲惫", "info": "信息阻断", "chan": "通道阻塞",
               "erode": "侵蚀", "deep": "纵深推进"}


def req_name(req: str) -> str:
    """组合的要求在界面上怎么写 (系列写系列的名字, 单个词条写去掉"环境:" / "目标:"之后的名字)."""
    if req in GROUP_NAMES:
        return GROUP_NAMES[req]
    name = BY_ID[req][3] if req in BY_ID else str(req)
    return name.split(":", 1)[-1].split("-", 1)[0]


def combo_done(combo, terms) -> bool:
    """这个词条组合凑齐了没有: 每一条要求都有一个选着的词条 (系列的任意一档都算)."""
    terms = [t for t in (terms or []) if t in BY_ID]
    for req in combo["reqs"]:
        hit = any((BY_ID[t][1] == req) if req in GROUPS else (t == req) for t in terms)
        if not hit:
            return False
    return True


def combo_state(combo, terms, orig=None) -> str:
    """组合的状态: done 凑齐了 / void 开始时凑齐过, 有成员失效了 / open 还没凑齐."""
    if combo_done(combo, terms):
        return "done"
    if orig is not None and combo_done(combo, orig):
        return "void"
    return "open"


def combo_level(terms) -> int:
    """凑齐的组合带来的额外危机等级."""
    return sum(int(c["level"]) for c in COMBOS if combo_done(c, terms))


def combo_rest(terms) -> float:
    """凑齐的组合带来的休息时间乘区."""
    f = 1.0
    for c in COMBOS:
        if combo_done(c, terms):
            f *= float(c["rest"])
    return f


def combo_gift(terms) -> int:
    """合约开始时凑齐的组合给的休息时间 (秒, 算合约中得到的)."""
    return int(sum(int(c["gift"]) for c in COMBOS if combo_done(c, terms)))


def combo_end(terms) -> int:
    """完成合约时凑齐的组合额外给的休息时间 (秒)."""
    return int(sum(int(c["end"]) for c in COMBOS if combo_done(c, terms)))


def combo_marks(terms) -> dict:
    """凑齐了的组合: 成员词条 → 组合的颜色 (界面上用这个颜色框住它们). 没凑齐的不算."""
    out = {}
    for c in COMBOS:
        if not combo_done(c, terms):
            continue
        for t in terms or []:
            if t not in BY_ID:
                continue
            if any((BY_ID[t][1] == req) if req in GROUPS else (t == req) for req in c["reqs"]):
                out.setdefault(t, COMBO_COLORS[c["color"]])
    return out


def level_of(terms) -> int:
    return sum(BY_ID[t][2] for t in terms if t in BY_ID) + combo_level(terms)


def life_of(terms) -> int:
    """选了这些词条时的初始目标生命值."""
    return min((LIFE[t] for t in terms or [] if t in LIFE), default=VIOLATION_LIMIT)


def rest_factor_of(terms) -> float:
    """合约中专注换来的休息时间乘多少: 基础 ×0.8, 有疲惫时换成疲惫的 (取最低的);
    再乘额外的乘区 (持久战 ×1.1/1.2/1.3, 禁止囤囤鼠! ×1.1, 打年糕 ×1.2; 都相乘)."""
    f = min((REST[t] for t in terms or [] if t in REST), default=BASE_REST)
    for t in terms or []:
        f *= REST_BONUS.get(t, 1.0)
    return f * combo_rest(terms)


def level_label(level: int, orig: int) -> str:
    """危机等级的写法: 扣过等级时写成 最终等级[最初等级-扣的等级], 例如 9[12-3]; 没扣过只写最终等级."""
    return f"{level}[{orig}-{orig - level}]" if orig > level else str(level)


def rest_minutes(level: int) -> int:
    lv = min(max(0, int(level)), REST_TOP)
    span = REST_MAX - REST_MIN
    return REST_MIN + (2 * span * lv + REST_TOP) // (2 * REST_TOP)    # 四舍五入 (.5 进位)


def rest_reward(level: int, terms=None) -> int:
    """完成合约时存进积攒的休息时间的秒数 (按等级查表 + 凑齐的组合的结束后收益)."""
    return rest_minutes(level) * 60 + combo_end(terms or [])


def rest_minutes_total(level: int, terms=None) -> int:
    """完成合约时休息时间一共多少分钟 (界面上写 "完成后休息 +N 分钟")."""
    return rest_reward(level, terms) // 60


def rest_gift(terms) -> int:
    """合约开始时存进积攒的休息时间的秒数 (凑齐的组合给的, 算合约中得到的)."""
    return combo_gift(terms)


def duration_of(terms) -> int:
    f = 1.0
    for t in terms:
        f = max(f, DURATION.get(t, 1.0))
    return int(round(BASE_SECONDS * f))


def conflicts(a: str, b: str) -> bool:
    if a == b or a not in BY_ID or b not in BY_ID:
        return False
    if BY_ID[a][1] == BY_ID[b][1]:
        return True
    return b in BY_ID[a][5] or a in BY_ID[b][5]


def blocked_by(tid: str, selected) -> str:
    """选了 selected 之后, tid 和其中哪一个冲突 (没有冲突返回 "")."""
    for s in selected:
        if s != tid and conflicts(tid, s):
            return s
    return ""


def needs_of(tid: str, selected) -> str:
    """tid 的前置条件 (NEEDS) 没有选着时返回它, 否则 ""."""
    need = NEEDS.get(tid, "")
    return need if need and need not in (selected or []) else ""


def clean_terms(terms, prereq: bool = False) -> list:
    """去掉不认识的和互相冲突的 (后面的让位给前面的), 按 TERMS 的顺序.
    prereq: 也去掉前置条件没选的 (选词条 / 开始合约时; 合约中前置失效了不去掉)."""
    out = []
    for t in terms or []:
        if t in BY_ID and t not in out and not blocked_by(t, out):
            out.append(t)
    if prereq:
        out = [t for t in out if not needs_of(t, out)]
    order = {t[0]: i for i, t in enumerate(TERMS)}
    return sorted(out, key=order.get)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def recent_completed(history, n: int = 5) -> list:
    """统计页用: 最近 n 个顺利完成的合约 (按结束时刻从新到旧); 黑雾时调用方自己造假数据."""
    done = [h for h in (history or [])
            if isinstance(h, dict) and h.get("result") == "completed" and h.get("end")]
    return sorted(done, key=lambda h: str(h.get("end")), reverse=True)[:max(0, int(n))]


def terms_of_record(rec: dict) -> list:
    """统计页看一个合约的词条: [(名字, 是不是失效了)], 按开始时的顺序.
    降级的按降级后的名字 (疲惫-III 降成 -II 就写 -II); 开始时选了、结束时已经失效的用原来的名字, 标成失效."""
    rec = rec if isinstance(rec, dict) else {}
    final = {}
    for t in rec.get("terms") or []:
        if t in BY_ID:
            final[BY_ID[t][1]] = t
    out = []
    for t in rec.get("orig") or []:
        if t not in BY_ID:
            continue
        ft = final.get(BY_ID[t][1])
        out.append((BY_ID[ft or t][3], ft is None))
    return out


def _parse(text):
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


# 黑雾 (18.3): 一天 48 个半小时各一句假内容. 只存编号, 显示时对词池长度取余 (中英文共用).
# FOG_POOL: 动词 / 量词 / 名词词池的大小 (同 pages/hour_log_page.py 和 Android 的词池); 一次抽的 48 句里每类词都不重复.
FOG_SLOTS = 48
FOG_POOL = (150, 80, 150)


def _fog_words(rnd) -> list:
    cols = [rnd.sample(range(n), FOG_SLOTS) for n in FOG_POOL]
    return [[c[i] for c in cols] for i in range(FOG_SLOTS)]

class Contract:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "contract.json")
        self.live = {"state": "none"}
        self.history = []
        self.draft = []
        # 时间重叠的两个合约只能留一个 (MECHANICS 15.5): 作废了的合约 id (随同步), 等用户选的冲突 (只在本机)
        self.voided = set()
        self.conflicts = []                   # [(a, b)]: 两个合约的摘要 (见 _brief), a 是本机原来的
        self._watch = {}                      # 软规则: 此刻是不是已经超了 (只记在本机)
        self.blocked = lambda: False          # 重置中: 不写文件
        self.load()

    # ---- 存档 ----

    def load(self):
        d = load_json_file(self.path, {})
        d = d if isinstance(d, dict) else {}
        live = d.get("live")
        self.live = live if isinstance(live, dict) and live.get("state") in ("active", "none") \
            else {"state": "none"}
        if self.active:
            self.live["terms"] = clean_terms(self.live.get("terms"))
        self.history = [h for h in (d.get("history") or []) if isinstance(h, dict) and h.get("start")]
        self.draft = clean_terms(d.get("draft"))
        self.voided = {str(x) for x in (d.get("void") or []) if x}
        self.conflicts = [(a, b) for a, b in (d.get("conflicts") or []) if isinstance(a, dict) and isinstance(b, dict)]

    def save(self):
        if self.blocked():
            return
        self.history = sorted(self.history, key=lambda h: str(h.get("end")))[-HISTORY_KEEP:]
        save_json_file(self.path, {"live": self.live, "history": self.history, "draft": self.draft,
                                   "void": sorted(self.voided), "conflicts": [list(c) for c in self.conflicts]})

    # ---- 进行中的合约 ----

    @property
    def active(self) -> bool:
        return self.live.get("state") == "active"

    @property
    def terms(self) -> list:
        return list(self.live.get("terms") or []) if self.active else []

    def has(self, tid: str) -> bool:
        return tid in self.terms

    @property
    def lost(self) -> list:
        """被违反到失效的词条 (失效前的最低一档; 不算未知变量选中的和录像)."""
        v = self.live.get("lost") if self.active else None
        return [t for t in v if t in BY_ID] if isinstance(v, list) else []

    def watching(self, tid: str) -> bool:
        """这个词条要不要检查: 选着, 或者已经被违反到失效."""
        return self.has(tid) or tid in self.lost

    def echo(self, tid: str) -> bool:
        """触发的是已经失效的词条: 只响警报, 不扣目标生命值."""
        return bool(tid) and not self.has(tid) and tid in self.lost

    def echo_due(self, tid: str, now: datetime = None) -> bool:
        """已失效的词条再次触发的警报: 每个词条 ECHO_COOLDOWN 秒最多响一次 (只记在本机)."""
        now = now or datetime.now()
        e = self.__dict__.setdefault("_echo", {})
        last = e.get(tid)
        if last is not None and (now - last).total_seconds() < ECHO_COOLDOWN:
            return False
        e[tid] = now
        return True

    def _lost_of(self, table) -> str:
        """已经失效的词条里属于 table 的那个 (这一类现在没有选着的)."""
        if any(t in table for t in self.terms):
            return ""
        return next((t for t in self.lost if t in table), "")

    @property
    def start_at(self):
        return _parse(self.live.get("start")) if self.active else None

    @property
    def end_at(self):
        return _parse(self.live.get("end")) if self.active else None

    @property
    def cut(self) -> dict:
        """只有一档的词条被降了几级."""
        c = self.live.get("cut") if self.active else None
        return c if isinstance(c, dict) else {}

    def term_level(self, tid: str) -> int:
        return BY_ID[tid][2] - int(self.cut.get(tid) or 0)

    @property
    def rand(self) -> list:
        """未知变量随机启用的词条 (不算进危机等级; 可以点击关闭, 算一次违反)."""
        r = self.live.get("rand") if self.active else None
        return [t for t in r if t in self.terms] if isinstance(r, list) else []

    @property
    def mine(self) -> list:
        """开始时选的词条现在还在的 (降级后算现在的; 不算未知变量选中的). 词条组合只看这些."""
        rand = self.rand
        return [t for t in self.terms if t in BY_ID and t not in rand]

    @property
    def level(self) -> int:
        mine = self.mine
        return sum(self.term_level(t) for t in mine) + combo_level(mine)

    @property
    def orig(self) -> list:
        """开始时选的词条 (降级前)."""
        return clean_terms(self.live.get("orig") or self.terms) if self.active else []

    @property
    def orig_level(self) -> int:
        return level_of(self.orig)

    def level_text(self) -> str:
        """合约页 / 侧边栏上的危机等级 (见 level_label); 没有进行中的合约时按草稿算."""
        return level_label(self.level, self.orig_level) if self.active else str(level_of(self.draft))

    @property
    def violations(self) -> int:
        return int(self.live.get("violations") or 0) if self.active else 0

    @property
    def violation_limit(self) -> int:
        """初始目标生命值."""
        return life_of(self.terms)

    @property
    def life(self) -> int:
        """目标生命值还剩多少 (每违反一次减一, 减到 0 合约失败)."""
        return max(0, self.violation_limit - self.violations)

    @property
    def rest(self) -> float:
        """合约中已经给出的休息时间 (秒, 已乘疲惫)."""
        return float(self.live.get("rest") or 0) if self.active else 0.0

    @property
    def reward(self) -> float:
        """完成时给的休息时间 (秒): 按现在的等级查表, 加上凑齐的组合的结束后收益; 结算按降级后的等级."""
        return float(rest_reward(self.level, self.mine)) if self.active else 0.0

    @property
    def owner(self) -> str:
        return str(self.live.get("owner") or "")

    def set_draft(self, terms):
        self.draft = clean_terms(terms, prereq=True)
        self.save()

    def start(self, terms, owner: str = "", now: datetime = None, pre: float = 0, cam: str = "") -> dict:
        """pre: 开始时积攒的休息时间 (秒). 合约中暂停休息时这部分不打折; 支援短缺限制它能用几成.
        cam: 目光由哪台设备的摄像头录 (设备 id, MECHANICS 18.8)."""
        now = (now or datetime.now()).replace(microsecond=0)
        terms = clean_terms(terms, prereq=True)
        pre = max(0.0, float(pre))
        share = self._supply_share(terms)
        self.live = {"state": "active", "id": _now_ms(), "start": _iso(now),
                     "end": _iso(now + timedelta(seconds=duration_of(terms))),
                     "terms": terms, "orig": list(terms), "cut": {}, "violations": 0, "rest": 0,
                     "owner": owner, "idle_since": None, "net": None, "first_break": False,
                     "pre": round(pre, 1), "pre_left": round(pre, 1), "pre_used": 0,
                     "supply": None if share is None else round(pre * share, 1), "hl": [],
                     "rand": [], "rand_n": 0, "fog_words": _fog_words(random.Random())}
        if "gaze" in terms and cam:
            self.live["cam"] = str(cam)
        self._watch = {}
        self.save()
        return self.live

    def finish(self, result: str, now: datetime = None, by: str = "") -> dict:
        """结束合约: result = completed / failed / idle / offline / abandoned. 返回这一条记录 (含给出的休息时间)."""
        if not self.active:
            return {}
        now = (now or datetime.now()).replace(microsecond=0)
        end = min(now, self.end_at or now)
        reward = int(round(self.reward)) if result == "completed" else 0
        rec = {"id": self.live.get("id"), "start": self.live.get("start"), "end": _iso(end),
               "planned": self.live.get("end"), "terms": self.terms, "orig": self.orig,
               "level": self.level, "orig_level": self.orig_level, "cut": dict(self.cut),
               "result": result, "violations": self.violations, "reward": reward,
               "pre_left": self.pre_left, "rand": self.rand, "owner": self.live.get("owner", "")}
        if self.live.get("locks"):
            rec["locks"] = list(self.live["locks"])        # 未知变量 / 记录封锁另外封锁的记录格 (记录用; 合约结束后解除)
        slow = self.live.get("slow")
        if isinstance(slow, dict) and slow.get("start"):
            rec["slow"] = slow["start"]                   # 阻滞的那段专注可能比合约结束得晚
        self.history.append(rec)
        self.live = {"state": "none", "owner": by, "first_break": result == "completed"}
        self.save()
        return rec

    @property
    def last(self) -> dict:
        """最近结束的一个合约."""
        return max(self.history, key=lambda h: str(h.get("end")), default={})

    # ---- 到期 / 空闲 ----

    def due(self, now: datetime, timer_idle: bool):
        """该结束了吗: ('completed' / 'idle', 结束的时刻) 或 ('', None).
        同时记下空闲从什么时候开始 (只由做自动结束的设备调用)."""
        if not self.active:
            return "", None
        end = self.end_at
        since = _parse(self.live.get("idle_since"))
        if timer_idle:
            if since is None:
                since = now
                self.live["idle_since"] = _iso(now)
                self.save()
        elif since is not None:
            self.live["idle_since"] = None
            self.save()
            since = None
        if since is not None and since + timedelta(seconds=IDLE_LIMIT) <= min(now, end):
            return "idle", since + timedelta(seconds=IDLE_LIMIT)
        if now >= end:
            return "completed", end
        return "", None

    def offline_due(self, devices, now: datetime, floor: datetime):
        """设备离线超过 OFFLINE_LIMIT: (设备名, 失败的时刻) 或 None. devices: [(名字, 离线开始的时刻)];
        离线从合约开始, 以及 floor (本机这次打开程序的时刻: 本机关着时的事由其他设备判定) 之后才算.
        空闲和设备离线共用这 15 分钟 (MECHANICS 18.1): 离线时合约也在空闲, 就从两者里先开始的那个算起."""
        if not self.active or not self.start_at:
            return None
        idle = _parse(self.live.get("idle_since"))
        for name, since in devices:
            since = max(since, self.start_at, floor)
            if idle is not None:
                since = min(since, max(idle, self.start_at))
            if since + timedelta(seconds=OFFLINE_LIMIT) <= now:
                return name, since + timedelta(seconds=OFFLINE_LIMIT)
        return None

    def idle_left(self, now: datetime):
        """空闲还能持续多久 (秒); 不在空闲时 None."""
        since = _parse(self.live.get("idle_since")) if self.active else None
        if since is None:
            return None
        return max(0.0, IDLE_LIMIT - (now - since).total_seconds())

    # ---- 网络禁令: 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (MECHANICS 18.3) ----

    def net_bans(self) -> list:
        """现在禁止联网的几类 (BANS 之一; 未知变量选中的也算)."""
        return [k for k in BANS if self.has(k)] if self.active else []

    def banned_exes(self) -> set:
        """现在禁止联网的程序 (exe 名, 小写)."""
        out = set()
        for k in self.net_bans():
            out |= set(NET_BAN_EXES.get(k, ()))
        return out

    def banned_domains(self) -> set:
        """现在封锁的网站 (域名, 子域名也算)."""
        out = set()
        for k in self.net_bans():
            out |= set(BAN_DOMAINS.get(k, ()))
        return out

    def can_lift(self, tid: str) -> bool:
        """点击词条可以解除网络禁令: 开始时选的 (未知变量选中的照旧是点击关闭)."""
        return self.active and tid in BANS and self.has(tid) and tid not in self.rand

    def lift_cost(self, tid: str) -> int:
        """解除网络禁令扣多少目标生命值: 词条现在的等级."""
        return max(1, self.term_level(tid))

    def lift_ban(self, tid: str):
        """解除网络禁令: 扣掉 lift_cost 点目标生命值, 去掉这个词条 (组合里算成员失效; 不算违反, 也不会再响警报).
        返回 (是否达到了上限, 扣了几点); 不能解除时 (False, 0)."""
        if not self.can_lift(tid):
            return False, 0
        n = self.lift_cost(tid)
        self.downgrade(tid, drop=True)
        self.live["violations"] = self.violations + n
        self.save()
        return self.violations >= self.violation_limit, n

    def violate(self, tid: str, drop: bool = False):
        """违反了一次词条 tid: 记一次, 这个词条降一级 (drop: 直接去掉, 关闭未知变量选中的词条).
        返回 (是否达到了上限, 降级后的词条 / "" = 失效). tid = "": 不是哪个词条的违反 (整点记录超时未填写), 只记一次."""
        if not self.active:
            return False, ""
        new = self.downgrade(tid, drop) if tid else ""
        self.live["violations"] = self.violations + 1
        self.save()
        return self.violations >= self.violation_limit, new

    def downgrade(self, tid: str, drop: bool = False) -> str:
        """词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效 (drop: 直接去掉).
        返回降级后的词条 ("" = 失效)."""
        terms = self.terms
        if tid not in terms:
            return ""
        old_share = self.net_share()
        old_supply = self._supply_share(terms)
        cut = dict(self.cut)
        rand = self.rand
        i = terms.index(tid)
        if drop:
            terms.pop(i)
            cut.pop(tid, None)
            new = ""
        elif tid in LOWER:
            terms[i] = new = LOWER[tid]
        elif tid in SINGLE and self.term_level(tid) > 1:
            cut[tid] = int(cut.get(tid) or 0) + 1
            new = tid
        else:
            terms.pop(i)
            cut.pop(tid, None)
            new = ""
        self.live["terms"] = clean_terms(terms)
        self.live["cut"] = cut
        gone = not new and not drop and tid not in rand and tid not in RECORD
        if gone and tid not in self.lost:    # 失效了照样检查, 再次触发只响警报
            self.live["lost"] = self.lost + [tid]
        if not new and tid in rand:          # 未知变量选中的词条因为违反 (或关闭) 失效: 可以再回到词条池一次
            viol = list(self.live.get("rand_viol") or [])
            if tid not in viol:
                self.live["rand_viol"] = viol + [tid]
        if tid in rand:                      # 未知变量选中的词条降级后还是它选中的 (移除的时刻不变)
            rand = [new if t == tid else t for t in rand if new or t != tid]
            self.live["rand"] = rand
            till = dict(self.live.get("rand_until") or {})
            at = till.pop(tid, None)
            if new and at is not None:
                till[new] = at
            self.live["rand_until"] = till
        # 信息阻断: 降一级 = 多给低一档多出来的那部分额度 (从现在用到的地方算起)
        n = self.live.get("net")
        new_share = self.net_share()
        if isinstance(n, dict) and old_share is not None and new_share is not None:
            n["limit"] = float(n.get("limit") or 0) + float(n.get("base") or 0) * (new_share - old_share)
        # 支援短缺: 同样多给低一档多出来的那部分; 失效了就不再限制
        new_supply = self._supply_share(self.live["terms"])
        if new_supply is None:
            if gone and tid in SUPPLY:       # 失效时的上限留着, 再次用超时响警报
                self.live["supply_lost"] = self.live.get("supply")
            self.live["supply"] = None
        elif old_supply is not None and self.live.get("supply") is not None:
            self.live["supply"] = round(float(self.live["supply"]) + self.pre * (new_supply - old_supply), 1)
        self.save()
        return new

    # ---- 未知变量: 合约的第 1, 2, 3 … 个小时开始时 (刚开始时也算) 随机启用一个词条 ----

    def random_candidates(self) -> list:
        """现在能被未知变量选中的词条: 1-2 级, 能选 (没有选着, 也不和选着的冲突), 不是录像;
        退路封锁不会让目标生命值直接归零. 不选已经选过的; 通过违反失效的词条可以再选中一次
        (选中期间违反照常扣目标生命值)."""
        terms = self.terms + list(self.waiting)
        used = set(self.live.get("rand_used") or [])
        viol = set(self.lost) | set(self.live.get("rand_viol") or [])
        again = set(self.live.get("rand_again") or [])
        out = []
        for t in TERMS:
            tid = t[0]
            if not (tid not in used and tid not in viol) and not (tid in viol and tid not in again):
                continue
            if t[2] not in RANDOM_LEVELS or tid in RANDOM_SKIP or tid in terms or blocked_by(tid, terms) \
                    or needs_of(tid, terms) or tid in self.waiting:
                continue
            if tid in LIFE and life_of(terms + [tid]) <= self.violations:
                continue
            out.append(tid)
        return out

    def roll(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> list:
        """到了新的一个小时就判定一次: 抽一个词条 (3 分钟后生效 (wake), 到这个小时结束时移除) 或者施加一个效果.
        返回这次的结果 [(种类, 值)]: ("term", 词条) / ("rest_cut", "") / ("rest_add", "") / ("lock", 封锁的格子键或 "")
        / ("double", "") / ("slow", "") / ("none", ""); 休息时间的增减由调用方做 (只由做自动结束的设备调用).
        程序关着时错过的小时依次补上 (那时抽中的词条到了它那个小时的结束就移除, 不再返回)."""
        if not self.active or not self.has(RANDOM) or not self.start_at:
            return []
        rng = rng or random
        end = self.end_at or now
        due = int((min(now, end) - self.start_at).total_seconds() // 3600) + 1 if now < end else 0
        done = int(self.live.get("rand_n") or 0)
        out = []
        while done < due:
            done += 1
            at = self.start_at + timedelta(hours=done - 1)
            self.expire(at)
            n, k = 1, 0
            while n > 0 and k < RANDOM_ROLLS:
                n, k = n - 1, k + 1
                pool = self.random_candidates()
                if pool and rng.random() < RANDOM_TERM_P:
                    tid = rng.choice(pool)
                    self._pick(tid, at)
                    out.append(("term", tid))
                    continue
                eff = self._effect(rng)
                if eff == "double":
                    n += 2
                elif eff == "slow":
                    self.live["slow"] = {"pending": True}
                elif eff == "lock":
                    out.append(("lock", self.lock_cell(now, rng, text_of, auto, fill)))
                    continue
                out.append((eff, ""))
            self.live["rand_n"] = done
            self.save()
        self.expire(now)
        wait = self.waiting
        return [e for e in out if e[0] != "term" or e[1] in wait]

    @staticmethod
    def _effect(rng) -> str:
        r = rng.random() * sum(w for _e, w in RANDOM_EFFECTS)
        for eff, w in RANDOM_EFFECTS:
            if r < w:
                return eff
            r -= w
        return RANDOM_EFFECTS[-1][0]

    def _pick(self, tid: str, at: datetime):
        """未知变量在 at 开始的这个小时抽中了 tid: 记下选过 (通过违反失效后再选中的记下用掉了这一次)."""
        viol = set(self.lost) | set(self.live.get("rand_viol") or [])
        used = list(self.live.get("rand_used") or [])
        if tid in viol:
            again = list(self.live.get("rand_again") or [])
            self.live["rand_again"] = again + [tid]
        if tid not in used:
            self.live["rand_used"] = used + [tid]
        wait = self.waiting
        wait[tid] = (at + RANDOM_DELAY, at + timedelta(hours=1))
        self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items()}

    # ---- 未知变量的效果 ----

    def lock_cell(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> str:
        """随机封锁一个还没被封锁, 已经过了填写时限的整点记录格 (只在合约期间内的, 合约结束后解除).
        返回格子的键 ("日期/小时", 细化规范的半小时 "日期/小时/1"); 没有可以封锁的返回 "".
        用 hour_units 取格子 —— 只有和合约重叠的真实格子, 不会多出合约开始前 (昨天) / 细化规范切出来的不存在的格子."""
        if not self.active or text_of is None or fill is None:
            return ""
        pool = [(k, a, b) for k, a, b in self.hour_units(now)
                if b + cell_fill(a, b, fill) <= now and not self.hour_locked(a, b, text_of(k) or "", now, fill, auto, k)]
        if not pool:
            return ""
        k, a, b = (rng or random).choice(pool)
        self.live["locks"] = list(self.live.get("locks") or []) + [[_iso(a), _iso(b)]]
        self.save()
        return k

    def seal_extra(self, now: datetime, rng=None, text_of=None, auto="", fill: timedelta = None) -> list:
        """记录封锁 (18.4): 每次完成填写时, 另外随机封锁 SEAL_EXTRA 个可以封锁的格子 (挑法同未知变量的 lock_cell).
        返回封锁了的格子的键; 没有选记录封锁 / 没有可以封锁的时少于 SEAL_EXTRA 个."""
        if not self.active or not self.has("seal"):
            return []
        out = []
        for _ in range(SEAL_EXTRA):
            k = self.lock_cell(now, rng, text_of, auto, fill)
            if not k:
                break
            out.append(k)
        return out

    def locks(self) -> list:
        """未知变量 / 记录封锁 另外封锁的记录格 (只算进行中的合约的; 合约结束后解除): [(开始, 结束)]."""
        out = []
        for src in [self.live.get("locks") if self.active else None]:
            for v in src or []:
                a, b = (_parse(v[0]), _parse(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)
                if a and b:
                    out.append((a, b))
        return out

    def slow_bind(self, start: datetime) -> bool:
        """开始专注: 等着的阻滞落到这段专注上 (专注时间按 RANDOM_SLOW 增长). 返回落上了没有.
        阻滞只对合约内的专注生效: 合约已经结束 / 这段专注开始于合约结束之后时不落上."""
        slow = self.live.get("slow") if self.active else None
        if not (isinstance(slow, dict) and slow.get("pending")):
            return False
        end = self.end_at
        if not end or start >= end:
            return False
        self.live["slow"] = {"start": _iso(start)}
        self.save()
        return True

    def slow_until(self, start: datetime):
        """阻滞让这段专注按 RANDOM_SLOW 增长到什么时候 (合约结束的时刻); 没有阻滞时 None."""
        if start is None:
            return None
        mark = _iso(start)
        slow = self.live.get("slow") if self.active else None
        if isinstance(slow, dict) and slow.get("start") == mark:
            return self.end_at
        for h in reversed(self.history[-3:]):
            if h.get("slow") == mark:
                return _parse(h.get("end"))
        return None

    def focus_rate(self, start: datetime, now: datetime = None) -> float:
        """开始于 start 的这段专注现在按多少增长: 阻滞 RANDOM_SLOW (只到合约结束), 之后 1."""
        until = self.slow_until(start)
        if until is None:
            return 1.0
        return RANDOM_SLOW if (now or datetime.now()) < until else 1.0

    def focus_minutes(self, start: datetime, seconds: float) -> float:
        """这一段专注计入的分钟数: 阻滞只减慢合约内的那部分时间, 合约结束后的部分按正常速度 (MECHANICS 18.3)."""
        seconds = max(0.0, float(seconds))
        until = self.slow_until(start)
        if until is None or start is None:
            return seconds / 60.0
        end = start + timedelta(seconds=seconds)
        inside = max(0.0, (min(end, until) - start).total_seconds())
        return (inside * RANDOM_SLOW + (seconds - inside)) / 60.0

    @property
    def slow_pending(self) -> bool:
        slow = self.live.get("slow") if self.active else None
        return isinstance(slow, dict) and bool(slow.get("pending"))

    def pre_cap(self, carry: float):
        """积攒的休息时间减少到 carry: 合约开始前的那部分最多剩这么多 (先减合约中得到的)."""
        if self.active and self.pre_left > float(carry):
            self.live["pre_left"] = round(max(0.0, float(carry)), 1)
            self.save()

    @property
    def waiting(self) -> dict:
        """未知变量抽中了, 还没生效的词条 → (生效的时刻, 移除的时刻)."""
        w = self.live.get("rand_wait") if self.active else None
        out = {}
        for tid, v in (w.items() if isinstance(w, dict) else ()):
            a, b = (_parse(v[0]), _parse(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)
            if tid in BY_ID and a and b:
                out[tid] = (a, b)
        return out

    def wake(self, now: datetime) -> list:
        """抽中的词条到了生效的时刻就启用 (到它那个小时结束时移除). 返回启用的词条."""
        wait = self.waiting
        due = [t for t, (a, b) in wait.items() if a <= now]
        if not due:
            return []
        self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items() if t not in due}
        out = []
        for t in due:
            a, b = wait[t]
            if now < b and t not in self.terms and not blocked_by(t, self.terms) and not needs_of(t, self.terms):
                self._enable(t, b)
                out.append(t)
        self.save()
        return out

    def _enable(self, tid: str, until: datetime = None):
        """合约中途启用一个词条: 开始时才定下来的效果在这里补上 (支援短缺: 能用多少; 信息阻断: 重新记下额度).
        until: 到这个时刻移除."""
        terms = clean_terms(self.terms + [tid])
        self.live["terms"] = terms
        self.live["rand"] = self.rand + [tid]
        if tid in HOUR_TERMS:
            self.live["hour_forced"] = True          # 整点记录在这次合约中一直开启 (词条移除后也是)
        till = dict(self.live.get("rand_until") or {})
        till.pop(tid, None)
        if until is not None:
            till[tid] = _iso(until)
        self.live["rand_until"] = till
        if tid in SUPPLY:
            self.live["supply"] = round(max(self.pre * self._supply_share(terms), self.pre_used), 1)
        if tid in NET_SHARE:
            self.live.pop("net", None)       # 之前选中过又移除了: 按现在的剩余额度重新算
        self.save()

    def rand_until(self, tid: str):
        """未知变量选中的这个词条什么时候移除 (选中的那个小时结束时); 一直有效时 None."""
        if tid not in self.rand:
            return None
        return _parse((self.live.get("rand_until") or {}).get(tid))

    def expire(self, now: datetime) -> list:
        """未知变量选中的词条: 那个小时结束了就移除 (不算违反; 还没生效的也去掉). 返回移除的 (已生效的) 词条."""
        wait = self.waiting
        if any(b <= now for a, b in wait.values()):
            self.live["rand_wait"] = {t: [_iso(a), _iso(b)] for t, (a, b) in wait.items() if b > now}
            self.save()
        till = dict(self.live.get("rand_until") or {}) if self.active else {}
        gone = [t for t, at in till.items() if _parse(at) is None or _parse(at) <= now]
        if not gone:
            return []
        rand = self.rand
        out = [t for t in gone if t in rand]
        self.live["terms"] = [t for t in self.terms if t not in out]
        self.live["rand"] = [t for t in rand if t not in out]
        self.live["rand_until"] = {t: at for t, at in till.items() if t not in gone}
        self.save()
        return out

    # ---- 软规则: 有没有违反 (由调用方算作违反: violate) ----

    def net_term(self) -> str:
        return next((t for t in self.terms if t in NET_SHARE), "")

    def net_check(self, used: float, bonus: float, raw: float, date: str):
        """信息阻断: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
        返回 (要降级的词条, "loan" / "over"), 没有违反时 ("", "") (只由做自动结束的设备调用, 额度是各设备共用的).
        已经失效的: 照样按失效时的额度看, 还在继续用超 / 又借贷了就返回它 (只响警报)."""
        tid = self.net_term() or self._lost_of(NET_SHARE)
        if not tid:
            return "", ""
        n = self.live.get("net")
        if not isinstance(n, dict) or n.get("date") != date:
            raw = max(0.0, float(raw))
            self.live["net"] = {"date": date, "base": round(raw, 1), "bonus": round(float(bonus), 1),
                                "limit": round(float(used) + raw * NET_SHARE[tid], 1)}
            self.save()
            return "", ""
        if float(bonus) > float(n.get("bonus") or 0) + 0.5:
            n["bonus"] = round(float(bonus), 1)
            self.save()
            return tid, "loan"
        over = float(used) > float(n.get("limit") or 0) + 0.5
        if tid in self.lost and not self.has(tid):
            w = self.__dict__.setdefault("_watch", {})
            last = w.get("net_used", float(used))
            w["net_used"] = float(used)
            over = over and float(used) > last + 0.5          # 失效后: 还在继续用才算
        if over:
            return tid, "over"
        return "", ""

    def net_left(self, used: float):
        """合约还允许用多少联网额度 (秒); 没有信息阻断时 None."""
        n = self.live.get("net") if self.net_term() else None
        if not isinstance(n, dict):
            return None
        return max(0.0, float(n.get("limit") or 0) - float(used))

    def _edge(self, key: str, over: bool) -> bool:
        """只在刚越过的那一下算一次违反; 回到限额以内后再越过才再算 (只记在本机)."""
        w = self.__dict__.setdefault("_watch", {})
        was = w.get(key, False)
        w[key] = over
        return over and not was

    def window_watch(self):
        """通道阻塞: (要检查的词条, 窗口数上限); 已经失效的按失效前的上限. 没有时 ("", 0)."""
        tid = next((t for t in self.terms if t in WINDOWS), "") or self._lost_of(WINDOWS)
        return tid, (WINDOWS[tid] if tid else 0)

    def window_check(self, n: int) -> str:
        """通道阻塞: 专注中任务栏上的窗口数超过词条的上限 (越过的那一下算一次)."""
        tid, lim = self.window_watch()
        if not lim or not tid:
            return ""
        return tid if self._edge("win", n > lim) else ""

    def window_reset(self):
        """离开专注时段: 通道阻塞的"越过"状态清掉, 下次专注重新算."""
        self._edge("win", False)

    def ban_term(self, kind: str) -> str:
        """禁止打开这一类软件 (BANS 之一) 的是哪个词条: 它自己; 不禁止时 ""."""
        return kind if self.watching(kind) else ""

    def ban_check(self, kind: str, hit: bool) -> str:
        """乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 开着这一类软件.
        每一类只在从没有到有的那一下算一次. 返回要降级的词条."""
        tid = self.ban_term(kind)
        if not tid:
            return ""
        return tid if self._edge(kind, hit) else ""

    def records(self, me: str = None) -> list:
        """合约中要录什么: camera (目光) / screen (刻痕).
        me: 本机的设备 id —— 目光只由开始时选定的那台设备 (cam) 录; 没有记下 cam 的 (旧版本开始的) 各台电脑都录."""
        out = [RECORD[t] for t in self.terms if t in RECORD]
        cam = self.cam
        if me is not None and cam and cam != me and "camera" in out:
            out.remove("camera")
        return out

    @property
    def cam(self) -> str:
        """目光由哪台设备的摄像头录 (设备 id; "" = 没有记下)."""
        return str(self.live.get("cam") or "") if self.active else ""

    def quick_check(self) -> str:
        """抑制: 用了快速访问."""
        return "suppress" if self.watching("suppress") else ""

    def focus_check(self, seconds: float) -> str:
        """纵深推进: 结束的这段专注不到要求的时长."""
        tid = next((t for t in self.terms if t in DEEP), "") or self._lost_of(DEEP)
        return tid if tid and float(seconds) < DEEP[tid] else ""

    def quiet_check(self, hit: bool) -> str:
        """静默协议: 专注中处于联网状态 (只在刚变成这样的那一下算一次)."""
        return "quiet" if self.watching("quiet") and self._edge("quiet", hit) else ""

    # ---- 合约开始前积攒的休息时间 (暂停不打折; 支援短缺) ----
    # 一次休息 = 建议休息时长 + 积攒的休息时间; 先用合约中得到的, 合约开始前积攒的放在最后用.

    @property
    def pre(self) -> float:
        """开始时积攒的休息时间 (秒)."""
        return float(self.live.get("pre") or 0) if self.active else 0.0

    @property
    def pre_left(self) -> float:
        """还在积攒的休息时间里的那部分 (没用掉, 也没有拿进正在进行的休息)."""
        return float(self.live.get("pre_left") or 0) if self.active else 0.0

    @property
    def pre_used(self) -> float:
        return float(self.live.get("pre_used") or 0) if self.active else 0.0

    def pre_take(self, carry: float) -> float:
        """开始休息, 积攒的 carry 秒全部拿进这次休息: 其中合约开始前的有多少 (从 pre_left 里拿走)."""
        if not self.active:
            return 0.0
        x = min(max(0.0, float(carry)), self.pre_left)
        if x > 0:
            self.live["pre_left"] = round(self.pre_left - x, 1)
            self.save()
        return x

    def pre_settle(self, taken: float, back: float):
        """一次休息结束 (back = 暂停时还给积攒的那部分合约开始前的时间, 自然结束时 0)."""
        if not self.active:
            return
        taken, back = max(0.0, float(taken)), max(0.0, min(float(taken), float(back)))
        self.live["pre_left"] = round(self.pre_left + back, 1)
        self.live["pre_used"] = round(self.pre_used + taken - back, 1)
        self.save()

    @staticmethod
    def _supply_share(terms):
        shares = [SUPPLY[t] for t in terms or [] if t in SUPPLY]
        return min(shares) if shares else None

    def supply_term(self) -> str:
        return next((t for t in self.terms if t in SUPPLY), "")

    @property
    def supply_limit(self):
        """支援短缺: 合约开始前积攒的休息时间最多能用多少秒 (None = 不限)."""
        v = self.live.get("supply") if self.supply_term() else None
        return None if v is None else float(v)

    def supply_until(self, left: float, taken: float):
        """正在休息 (还剩 left 秒, 这次休息里有 taken 秒是合约开始前的): 再过多少秒就用超了 (None = 这次用不超)."""
        lim = self.supply_limit
        if lim is None or taken <= 0:
            return None
        cross = float(taken) - (lim - self.pre_used)       # 剩余时间降到这里时用超
        if cross <= 0:
            return None
        return max(0.0, float(left) - cross)

    def supply_watch(self) -> str:
        """支援短缺要检查的词条: 选着的, 或者已经失效的 (按失效时的上限)."""
        if self.supply_term():
            return self.supply_term()
        return self._lost_of(SUPPLY) if self.live.get("supply_lost") is not None else ""

    def supply_check(self, left: float, taken: float) -> str:
        """支援短缺: 合约开始前积攒的休息时间用超了 (正在休息时按已经用掉的算).
        已经失效的: 按失效时的上限, 还在继续用就返回它 (只响警报)."""
        used = self.pre_used + max(0.0, float(taken) - max(0.0, float(left)))
        tid = self.supply_term()
        if not tid:
            tid = self.supply_watch()
            if not tid:
                return ""
            w = self.__dict__.setdefault("_watch", {})
            last = w.get("supply_used", used)
            w["supply_used"] = used
            return tid if used > float(self.live["supply_lost"]) + 0.5 and used > last + 0.5 else ""
        lim = self.supply_limit
        if lim is None:
            return ""
        return tid if used > lim + 0.5 else ""

    # ---- 词条的效果 ----

    def overrides(self) -> dict:
        """合约中盖过设置的项 (config 里的值, 硬规则): 全频道阻塞干扰 → 断网方式换成
        代理-全屏蔽 (Proxy block-all), 合约结束后回到设置里的断网方式."""
        if not self.has("jam"):
            return {}
        return {"NET_MODE": "proxy_all"}

    def window_limit(self) -> int:
        return min((WINDOWS[t] for t in self.terms if t in WINDOWS), default=0)

    def net_share(self):
        """只能用剩余联网时间的几成 (None = 不限)."""
        shares = [NET_SHARE[t] for t in self.terms if t in NET_SHARE]
        return min(shares) if shares else None

    def fog(self) -> bool:
        return self.has("fog")

    def fog_words(self) -> list:
        """黑雾时整点记录的假内容: 开始合约时抽好的 FOG_SLOTS 组 [动词, 量词, 名词] 编号 (随合约同步, 两端一样).
        以前开始的合约没有这一项: 用合约 id 当种子补一份."""
        w = self.live.get("fog_words") if self.active else None
        if isinstance(w, list) and w and all(isinstance(x, list) and len(x) == 3 for x in w):
            return w
        return _fog_words(random.Random(str(self.live.get("id"))))

    def hoard_check(self) -> str:
        """禁止囤囤鼠!: 提前结束了休息 (软规则: 照样能结束, 算一次违反)."""
        return "hoard" if self.watching("hoard") else ""

    def cooldown_factor(self) -> float:
        """侵蚀: 网络申请的冷静期乘多少."""
        return max((COOLDOWN_X[t] for t in self.terms if t in COOLDOWN_X), default=1.0)

    def hour_forced(self) -> bool:
        """选了整点记录的词条: 整点记录关着时, 这次合约中照样开启. 未知变量启用过整点记录的词条: 这次合约中一直开启."""
        return any(t in HOUR_TERMS for t in self.terms) or (self.active and bool(self.live.get("hour_forced")))

    def fine_hour(self, slot_start: datetime) -> bool:
        """细化规范: 这一小时 (按开始时刻) 和进行中的合约重叠, 记录改为每 30 分钟一次."""
        if not self.has("fine"):
            return False
        a, b = self.start_at, self.end_at
        return bool(a and b and a < slot_start + timedelta(hours=1) and slot_start < b)

    def rest_factor(self) -> float:
        return rest_factor_of(self.mine)

    def combos(self) -> list:
        """(组合, 状态) 现在的样子: 效果栏 (第 3 页) 照着画."""
        terms, orig = self.mine, (self.orig if self.active else [])
        return [(c, combo_state(c, terms, orig if self.active else None)) for c in COMBOS]

    def combo_marks(self) -> dict:
        """凑齐了的组合的成员词条 → 颜色 (界面上框住它们)."""
        return combo_marks(self.mine)

    def book_rest(self, seconds: float, end: datetime = None) -> float:
        """一段专注换来的休息时间 (秒, 已乘"休息时间倍率"). 返回给出的休息时间.
        合约中: 乘疲惫的倍率 (记进合约里给出的休息时间). 完成合约的奖励另外按等级给 (reward)."""
        seconds = max(0.0, float(seconds))
        end = end or datetime.now()
        if self.active and self.start_at and self.start_at <= end:
            now = seconds * self.rest_factor()
            self.live["rest"] = round(self.rest + now, 1)
            self.save()
            return now
        return seconds

    def book_rest_after(self, seconds: float, start: datetime) -> float:
        """完成之后才结束的专注, 开始于刚完成的合约里: 按那个合约的疲惫给 (未知变量选中的不算组合)."""
        h = self.last
        hs, he = _parse(h.get("start")), _parse(h.get("end"))
        if h.get("result") == "completed" and hs and he and hs <= start < he:
            rand = set(h.get("rand") or [])
            terms = [t for t in (h.get("terms") or []) if t not in rand]
            return seconds * rest_factor_of(terms)
        return seconds

    def take_first_break(self) -> bool:
        """完成合约后的第一次休息: 用掉这个标记."""
        if self.active or not self.live.get("first_break"):
            return False
        self.live["first_break"] = False
        self.save()
        return True

    @staticmethod
    def first_break_rate(x: float) -> float:
        """完成合约后的第一次休息: 暂停时的结转比例 1 - (1 - x) / 2."""
        x = min(1.0, max(0.0, float(x)))
        return 1.0 - (1.0 - x) / 2.0

    # ---- 整点记录: 超时未填写算一次违反; 记录封锁 ----

    def periods(self, now: datetime = None, term: str = ""):
        """各个合约 (结束了的和进行中的) 的 (开始, 结束); term: 只要选了这个词条的."""
        out = []
        for h in self.history:
            a, b = _parse(h.get("start")), _parse(h.get("end"))
            if a and b and (not term or term in (h.get("orig") or h.get("terms") or [])):
                out.append((a, b))
        if self.active and self.start_at and (not term or term in self.orig):
            out.append((self.start_at, now or datetime.now()))
        return out

    def hour_units(self, now: datetime):
        """进行中的合约里已经结束的记录格: [(键, 开始, 结束)].
        键是 "日期/小时", 细化规范的两个半小时是 "日期/小时/1" "日期/小时/2"."""
        if not self.active or not self.start_at:
            return []
        a, b = self.start_at, min(now, self.end_at or now)
        out = []
        h = a.replace(minute=0, second=0, microsecond=0)
        while h < b:
            key = f"{h:%Y-%m-%d}/{h.hour}"
            if self.fine_hour(h):
                parts = [(key + "/1", h, h + timedelta(minutes=30)),
                         (key + "/2", h + timedelta(minutes=30), h + timedelta(hours=1))]
            else:
                parts = [(key, h, h + timedelta(hours=1))]
            out += [p for p in parts if p[1] < b and a < p[2] and p[2] <= now]
            h += timedelta(hours=1)
        return out

    def hour_misses(self, now: datetime, fill: timedelta, text_of, auto) -> list:
        """合约中过了填写时限还没填写的记录格 (空着, 或者是超时自动记录的内容): 每格只算一次违反.
        返回新算的键 (只由做自动结束的设备调用).
        auto: 超时自动记录的内容, 或者 (键) → 那一小时的自动记录内容的函数 (行为模式)."""
        if not self.active:
            return []
        old = list(self.live.get("hl") or [])
        seen = set(old)
        new = []
        for key, a, b in self.hour_units(now):
            if key in seen or b + cell_fill(a, b, fill) > now:
                continue
            seen.add(key)
            if self._is_auto(text_of(key) or "", key, auto):
                new.append(key)
        if len(seen) != len(old):
            self.live["hl"] = sorted(seen)
            self.save()
        return new

    @staticmethod
    def _is_auto(text: str, key: str, auto) -> bool:
        try:
            wanted = str(auto(key)) if callable(auto) else str(auto or "")
        except Exception:
            wanted = "" if callable(auto) else str(auto or "")
        return str(text or "") in ("", wanted)

    def hour_sealed(self, start: datetime, end: datetime, now: datetime = None) -> bool:
        """这个记录格在记录封锁的范围里 (填写后就不能再改, 界面上提交前先确认):
        只在进行中、选了记录封锁的合约里, 和这个合约重叠的格子. 合约结束后 / 没选记录封锁时都不算."""
        if not self.active or not self.start_at or not ("seal" in self.orig or self.has("seal")):
            return False
        return self.start_at < end and start < (now or datetime.now())

    def hour_locked(self, start: datetime, end: datetime, text: str, now: datetime, fill: timedelta,
                    auto, key: str = "") -> bool:
        """记录封锁: 进行中的合约选了这个词条时, 和合约重叠的记录格已经填写了的 (自己填的, 或者超时自动记录的) 就不能再改,
        没填的照常能填. 未知变量 / 记录封锁另外封锁的格子不管填没填都不能再改. 合约结束后全部恢复."""
        if any(a < end and start < b for a, b in self.locks()):
            return True
        if not str(text or "").strip():
            return False
        return self.hour_sealed(start, end, now)

    # ---- 设备同步 ----

    def doc(self) -> dict:
        out = {"live": dict(self.live), "history": [dict(h) for h in self.history]}
        for vid in self.voided:
            out["void:" + vid] = 1                # 每个作废的 id 单独一项: 同步时只会增加
        return out

    # ---- 时间重叠的两个合约 (例如一台设备用 Adapter 断网期间各自开始了合约, MECHANICS 15.5) ----

    @staticmethod
    def _brief(rec: dict, live: bool) -> dict:
        """冲突里一个合约的摘要: id, 起止, 危机等级, 词条, 结果 ("" = 进行中); 进行中的带上整个 live (保留它时恢复)."""
        terms = list(rec.get("terms") or [])
        rand = set(rec.get("rand") or [])
        mine = [t for t in terms if t in BY_ID and t not in rand]
        out = {"id": str(rec.get("id")), "start": rec.get("start"), "end": rec.get("end"),
               "level": int(rec.get("level") or (sum(BY_ID[t][2] for t in mine) + combo_level(mine))),
               "terms": terms, "result": "" if live else str(rec.get("result") or "")}
        if live:
            out["live"] = dict(rec)
        return out

    @staticmethod
    def _overlap(a: dict, b: dict) -> bool:
        sa, ea, sb, eb = _parse(a.get("start")), _parse(a.get("end")), _parse(b.get("start")), _parse(b.get("end"))
        return bool(sa and ea and sb and eb and sa < eb and sb < ea)

    def _add_conflict(self, mine: dict, theirs: dict):
        ids = {mine["id"], theirs["id"]}
        if mine["id"] == theirs["id"] or ids & self.voided:
            return
        if any({a["id"], b["id"]} == ids for a, b in self.conflicts):
            return
        self.conflicts.append((mine, theirs))

    def resolve_conflict(self, keep_id: str) -> bool:
        """用户选了留下 keep_id: 另一个作废 (进行中的直接结束, 不给奖励; 记录里删掉), 留下的要是被盖掉的进行中合约就恢复它.
        已经给出的休息时间不收回. 返回有没有处理."""
        keep_id = str(keep_id)
        hit = next((c for c in self.conflicts if keep_id in (c[0]["id"], c[1]["id"])), None)
        if hit is None:
            return False
        keep = hit[0] if hit[0]["id"] == keep_id else hit[1]
        drop = hit[1] if keep is hit[0] else hit[0]
        self.voided.add(drop["id"])
        self._apply_voids()
        if keep.get("live") and not self.active and keep_id not in {str(h.get("id")) for h in self.history}:
            self.live = dict(keep["live"])        # 留下的是被同步盖掉的那个进行中合约: 恢复 (之后随同步)
            self.live["terms"] = clean_terms(self.live.get("terms"))
        self.save()
        return True

    def _apply_voids(self) -> bool:
        """作废的合约: 进行中的结束 (不给奖励, 不留记录), 记录里删掉, 相关的冲突不用再问. 返回进行中的合约变了没有."""
        changed = False
        if self.active and str(self.live.get("id")) in self.voided:
            self.live = {"state": "none", "owner": self.live.get("owner") or ""}
            self._watch = {}
            changed = True
        self.history = [h for h in self.history if str(h.get("id")) not in self.voided]
        self.conflicts = [(a, b) for a, b in self.conflicts if not ({a["id"], b["id"]} & self.voided)]
        return changed

    def merge(self, changes: dict) -> bool:
        """同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id). 返回进行中的合约是否变了.
        两个不同的合约时间重叠 (进行中的被另一个盖掉, 或者送来的记录和本机的重叠): 记下冲突, 让用户选留哪个 (15.5)."""
        changed = False
        for key in changes:
            if str(key).startswith("void:") and changes[key]:
                self.voided.add(str(key)[5:])
        mine_live = self._brief(self.live, True) if self.active else None
        ended = {str(h.get("id")) for h in (changes.get("history") or []) if isinstance(h, dict)}
        ended |= {str(h.get("id")) for h in self.history}
        if mine_live and mine_live["id"] in ended:
            mine_live = None                         # 本机这个合约在别的设备上已经结束了: 正常切换, 不算冲突
        if isinstance(changes.get("live"), dict) and changes["live"].get("state") in ("active", "none"):
            new = changes["live"]
            if str(new.get("id")) in self.voided and new.get("state") == "active":
                new = None                           # 已经作废的合约又被送回来: 不收
            if new is not None and new != self.live:
                if mine_live and new.get("state") == "active" and str(new.get("id")) != mine_live["id"]:
                    self._add_conflict(mine_live, self._brief(new, True))
                self.live = dict(new)
                if self.active:
                    self.live["terms"] = clean_terms(self.live.get("terms"))
                changed = True
        if isinstance(changes.get("history"), list):
            have = {str(h.get("id")) for h in self.history}
            local = [self._brief(h, False) for h in self.history] + ([mine_live] if mine_live else [])
            for h in changes["history"]:
                hid = str(h.get("id")) if isinstance(h, dict) else ""
                if isinstance(h, dict) and h.get("start") and hid not in have and hid not in self.voided:
                    for loc in local:
                        if loc["id"] != hid and self._overlap(loc, h):
                            self._add_conflict(loc, self._brief(h, False))
                    self.history.append(dict(h))
                    have.add(hid)
        if self._apply_voids():
            changed = True
        self.save()
        return changed


def cell_fill(start: datetime, end: datetime, fill: timedelta) -> timedelta:
    """记录格的补填时限: 细化规范的半小时格只有 30 min (MECHANICS 18.4)."""
    return min(fill, timedelta(minutes=30)) if end - start <= timedelta(minutes=30) else fill


def _selftest():
    import tempfile
    assert level_of(["long3", "tired3", "info3", "last"]) == 12, "最后防线没有额外等级了, 只有初始目标生命值 1"
    assert level_of(["long3", "tired3"]) == 6 and level_of(["last", "long1"]) == 4
    assert life_of([]) == 4 and life_of(["retreat1"]) == 3 and life_of(["retreat2"]) == 2 and life_of(["last"]) == 1
    assert conflicts("retreat1", "last") and conflicts("retreat2", "last"), "退路封锁和最后防线是同一组"
    assert not {"vlc.exe", "potplayermini64.exe", "mpc-hc64.exe", "wmplayer.exe"} & MUSIC_EXES, "视频播放器不算"
    assert level_label(9, 12) == "9[12-3]" and level_label(12, 12) == "12" and level_label(13, 12) == "13"
    assert rest_minutes(0) == 20 and rest_minutes(1) == 21 and rest_minutes(4) == 24 and rest_minutes(19) == 40
    assert rest_minutes(37) == 59 and rest_minutes(38) == 60 and rest_minutes(50) == 60 and rest_reward(45) == 60 * 60
    assert all(rest_minutes(i) <= rest_minutes(i + 1) for i in range(40))
    # 删掉的两个 4 级词条: 互联网娱乐的败退 变成组合, 信息隔绝 (旧词条) 删了
    assert "isolate" not in BY_ID and "ent" not in BY_ID and MAX_LEVEL == 3, "没有了 4 级的词条: 词条板不再留那一行"
    assert BY_ID["silent"][2] == 2, "无言为真调整为 2 级"
    assert (NET_SHARE["info1"], NET_SHARE["info2"], NET_SHARE["info3"]) == (0.8, 0.65, 0.5), "信息阻断: 80% 65% 50%"
    assert conflicts("long1", "long3") and conflicts("short1", "short2"), "同一组互相冲突"
    assert not conflicts("erode1", "info3") and not conflicts("suppress", "quiet") and not conflicts("quiet", "jam")
    assert not conflicts("erode1", "info2") and not conflicts("suppress", "fog")
    assert not conflicts("jam", "music") and not conflicts("seal", "fine")
    assert clean_terms(["long2", "long1", "x", "suppress", "jam"]) == ["long2", "suppress", "jam"]
    assert duration_of([]) == 4 * 3600 and duration_of(["long3"]) == int(4 * 3600 * 1.6)
    # 词条板: 每个词条正好一格, 一列里每个等级最多一个; 同一组 (系列) 在同一列; 两页各 18 个词条
    assert sorted(t for col in BOARD for t in col) == sorted(BY_ID) and len(set(LAYOUT.values())) == len(TERMS)
    assert all([BY_ID[t][2] for t in col] == sorted({BY_ID[t][2] for t in col}) for col in BOARD)
    assert all(len({LAYOUT[t][0] for t in BY_ID if BY_ID[t][1] == g}) == 1 for g in GROUPS)
    assert len(BOARD) == 20 and [a for a, b in PAGES] + [PAGES[-1][1]] == [0, 10, 20], "两页各 10 列"
    assert [sum(len(col) for col in BOARD[a:b]) for a, b in PAGES] == [23, 13]
    assert [t[0] for t in TERMS] == [t for col in BOARD for t in col], "TERMS 的顺序同词条板"
    # 相似的放在一起: 无言为真在乐理之灾下面, 细化规范在记录封锁下面, 全频道阻塞干扰在抑制下面 (目光 / 刻痕现在都是 1 级, 各占一列)
    for up, down in (("music", "silent"), ("seal", "fine"), ("suppress", "jam"), ("fog", "unknown")):
        assert LAYOUT[up][0] == LAYOUT[down][0], (up, down)
    assert [LAYOUT[t][0] for t in ("music", "game", "video", "gaze", "scar", "move", "confine")] == \
        list(range(LAYOUT["music"][0], LAYOUT["music"][0] + 7)), "网络禁令, 录像, 现实挨着"
    assert LAYOUT["hoard"][0] == LAYOUT["short1"][0] + 1 and LAYOUT["chan1"][0] == LAYOUT["deep1"][0] + 1
    assert LAYOUT["deep1"][0] < PAGE_SPLIT and LAYOUT["info1"][0] < PAGE_SPLIT <= LAYOUT["suppress"][0] and LAYOUT["seal"][0] < PAGE_SPLIT
    # 高机动只留 1 级 (旧的高机动-I / II 删掉了), 和禁足挨着; 记录封锁的描述换掉了
    assert "move1" not in BY_ID and "move2" not in BY_ID
    assert BY_ID["move"][2] == 1 and BY_ID["move"][4] == "现实中的移动速度+50%"
    assert BY_ID["confine"][2] == 1 and BY_ID["confine"][4] == "在非休息时间不能离开工作岗位进行活动"
    assert LAYOUT["confine"][0] == LAYOUT["move"][0] + 1, "高机动 / 禁足 挨着"
    assert BY_ID["seal"][4] == "无法修改已经被填写的格子 (包括自动填写和手动填写); 每次完成填写时, 另外随机封锁两个可以封锁的格子"
    # 目光 / 刻痕: 都是 1 级, 不再冲突; 高机动 / 禁足的前置条件是目光
    assert BY_ID["gaze"][2] == 1 and BY_ID["scar"][2] == 1 and not conflicts("gaze", "scar") and not conflicts("gaze", "music")
    assert needs_of("move", []) == "gaze" and needs_of("confine", ["scar"]) == "gaze" and needs_of("move", ["gaze"]) == ""
    assert needs_of("music", []) == "" and clean_terms(["move", "music"]) == ["music", "move"]
    assert clean_terms(["move", "confine", "music"], prereq=True) == ["music"], "没选目光: 高机动 / 禁足去掉"
    assert clean_terms(["confine", "gaze", "scar"], prereq=True) == ["gaze", "scar", "confine"]
    assert conflicts("hoard", "short1") and conflicts("short2", "hoard"), "禁止囤囤鼠! 和支援短缺冲突"
    assert clean_terms(["short1", "hoard"]) == ["short1"]
    assert not conflicts("silent", "music") and not conflicts("silent", "game"), "禁止软件的词条可以一起选"

    # 词条组合: 系列任意一档都算; 凑齐了加危机等级 / 乘区 / 开始时或完成时的休息时间
    mochi, nofun, flawless, isolation = (COMBOS_BY_ID[i] for i in ("mochi", "nofun", "flawless", "isolation"))
    assert combo_done(mochi, ["long1", "tired2", "fine", "deep1"])
    assert not combo_done(mochi, ["long1", "tired2", "fine"]) and not combo_done(mochi, ["long1", "tired2"])
    assert combo_state(mochi, ["long1", "tired2"]) == "open"
    assert combo_state(mochi, ["long1", "tired2"], ["long1", "tired2", "fine", "deep1"]) == "void"
    assert combo_state(mochi, ["long1", "tired2", "fine", "deep1"]) == "done"
    assert combo_level(["long1", "tired2", "fine", "deep1"]) == 1
    assert abs(rest_factor_of(["long1", "tired2", "fine", "deep1"]) - 0.6 * 1.1 * 1.2) < 1e-9
    iso = ["info1", "erode1", "chan1", "suppress", "fog", "quiet", "jam"]
    assert sorted(clean_terms(iso)) == sorted(iso), "信息隔绝组合的词条可以一起选"
    assert combo_done(isolation, iso) and combo_level(iso) == 1 and combo_end(iso) == 10 * 60
    assert rest_reward(6, iso) == rest_minutes(6) * 60 + 10 * 60
    assert rest_minutes_total(6, iso) == rest_minutes(6) + 10
    assert combo_gift(["music", "silent", "game", "video"]) == 20 * 60, "互联网娱乐的败退: 开始时 +20 分钟"
    assert combo_gift(["last", "unknown"]) == 0, "无懈可击: 还差 禁足 / 高机动 / 目光 / 刻痕"
    assert combo_gift(["last", "unknown", "move", "confine"]) == 0, "无懈可击: 还差 目光 / 刻痕"
    flaw = ["last", "unknown", "move", "confine", "gaze", "scar"]
    assert combo_gift(flaw) == 20 * 60, "无懈可击: 开始时 +20 分钟"
    assert combo_marks(flaw) == {t: COMBO_COLORS["gray"] for t in flaw}
    assert combo_marks(["long1", "tired2"]) == {}
    assert nofun["color"] == "purple" and flawless["color"] == "gray" and isolation["color"] == "red" \
        and mochi["color"] == "white"
    assert req_name("deep") == "纵深推进" and req_name("last") == "最后防线" and req_name("silent") == "无言为真"
    # 统计页: 最近 5 个顺利完成的合约 (失败的 / 提前结束的不要)
    hist = [{"end": f"2026-10-{i:02d}T10:00:00", "result": r, "level": i, "orig_level": i + 2}
            for i, r in enumerate(["completed", "failed", "completed", "abandoned",
                                   "completed", "completed", "completed"], 1)]
    got = recent_completed(hist, 5)
    assert [h["level"] for h in got] == [7, 6, 5, 3, 1], got
    assert recent_completed(hist, 2) == got[:2] and recent_completed([]) == []
    assert recent_completed([{"end": "", "result": "completed"}]) == [], "没有结束时刻的不算"
    # 统计页看词条: 降级的按降级后的名字, 失效的标出来
    rec = {"orig": ["long3", "tired3", "silent", "music"], "terms": ["long3", "tired2", "silent"]}
    assert terms_of_record(rec) == [("环境:持久战-III", False), ("环境:疲惫-II", False),
                                    ("环境:无言为真", False), ("环境:乐理之灾", True)]
    assert terms_of_record({}) == [] and terms_of_record(None) == []
    for t in ("ChatGPT", "Claude", "(3) 首页 / X", "如何评价…？ - 知乎 - Google Chrome", "微博-随时随地发现新鲜事",
              "DeepSeek - 探索未至之境 - Microsoft Edge", "Reddit - Dive into anything", "Kimi 智能助手"):
        assert site_title(t, SOCIAL_TITLES), t
    for t in ("CLAUDE.md - obliphur_terminal - Visual Studio Code", "知乎 - Google 搜索", "新标签页",
              "Claude Code 文档", "微信读书", "Example Domain", "poem - Wikipedia", "【4K】某视频_哔哩哔哩_bilibili"):
        assert not site_title(t, SOCIAL_TITLES), t
    for t in ("【4K】某视频_哔哩哔哩_bilibili - Google Chrome", "(12) Some video - YouTube", "爱奇艺-在线视频网站"):
        assert site_title(t, VIDEO_TITLES), t
    assert not site_title("youtube-dl - GitHub", VIDEO_TITLES) and not site_title("ChatGPT", VIDEO_TITLES)
    assert not set(SOCIAL_EXES) & set(VIDEO_EXES) and not SOCIAL_TITLES & VIDEO_TITLES, "一个软件只算一个词条"
    best = clean_terms(["long3", "erode2", "info3", "suppress", "jam", "music", "fog", "tired3", "chan2", "last",
                        "short2", "seal", "fine", "silent", "game", "video", "gaze", "scar"])
    assert "scar" in best and level_of(best) == 32, level_of(best)
    assert combo_gift(best) == 20 * 60, "互联网娱乐的败退凑齐了"
    best2 = clean_terms(["info3", "chan2", "erode2", "suppress", "fog", "quiet", "jam", "last", "long3", "tired3", "fine"])
    assert len(best2) == 11, best2
    assert combo_level(best2) == 1 and level_of(best2) == 23 + 1, level_of(best2)

    c = Contract(tempfile.mkdtemp())
    t0 = datetime(2026, 10, 18, 9, 30)
    c.start(["tired2", "info2", "chan1", "long1", "music"], owner="me", now=t0)
    assert c.active and c.level == 2 + 2 + 1 + 1 + 1 and c.end_at == t0 + timedelta(seconds=int(4 * 3600 * 1.2))
    assert c.overrides() == {} and not c.fog() and not c.hour_forced()
    # 信息阻断-II: 开始时剩 100 分钟, 只能用 65 分钟; 用超了 → 降成 -I, 多给 15 分钟
    assert c.net_check(600, 0, 6000, "2026-10-18") == ("", "")
    assert c.net_left(600) == 3900 and c.net_check(4499, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(4600, 0, 0, "2026-10-18") == ("info2", "over")
    over, new = c.violate("info2")
    assert not over and new == "info1" and c.terms == ["long1", "chan1", "tired2", "info1", "music"], c.terms
    assert c.net_left(4600) == 600 + 3900 + 900 - 4600 and c.level == 6 and c.orig_level == 7
    assert c.net_check(4700, 0, 0, "2026-10-18") == ("", "")
    assert c.net_check(4700, 600, 0, "2026-10-18") == ("info1", "loan"), "借贷也算违反"
    assert c.net_check(4700, 600, 0, "2026-10-18") == ("", ""), "同一次借贷只算一次"
    assert c.downgrade("info1") == "" and not c.net_term() and c.net_check(9999, 9999, 0, "x") == ("", "")
    # 通道阻塞 / 乐理之灾: 只在刚越过的那一下算
    assert c.window_check(5) == "chan1" and c.window_check(6) == "" and c.window_check(3) == ""
    assert c.window_check(5) == "chan1"
    assert c.ban_check("music", True) == "music" and c.ban_check("music", True) == "" and c.ban_check("music", False) == ""
    assert c.downgrade("music") == "" and not c.has("music"), "乐理之灾只有 1 级, 降一级就失效"
    # 无言为真 (2 级) / 禁游令: 违反一次降一级, 再违反才失效; 录像 (目光 / 刻痕) 违反一次就失效
    # 时间重叠的两个合约 (15.5): 进行中的被同步盖掉 → 记下冲突; 选了留下哪个, 另一个作废 (随同步)
    ka, kb = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    ka.start(["long1"], owner="A", now=t0)
    kb.start(["music", "game"], owner="B", now=t0 + timedelta(minutes=30))
    a_id, b_id = str(ka.live["id"]), str(kb.live["id"])
    assert a_id != b_id
    assert ka.merge({"live": kb.doc()["live"]}) and ka.live["id"] == kb.live["id"]
    assert len(ka.conflicts) == 1 and {ka.conflicts[0][0]["id"], ka.conflicts[0][1]["id"]} == {a_id, b_id}
    assert ka.resolve_conflict(a_id) and str(ka.live["id"]) == a_id and b_id in ka.voided and not ka.conflicts
    assert ka.doc()["void:" + b_id] == 1
    assert kb.merge({"void:" + b_id: 1}) and not kb.active, "作废的进行中合约在另一台设备上也结束"
    assert not kb.merge({"live": dict(ka.live, id=int(b_id))}) or str(kb.live.get("id")) != b_id, "作废的不再收"
    # 送来的记录和本机的记录时间重叠 → 冲突; 留下送来的, 本机的删掉
    kc, kd = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    kc.start(["long1"], owner="C", now=t0)
    kc.finish("completed", t0 + timedelta(hours=2))
    kd.start(["fog"], owner="D", now=t0 + timedelta(hours=1))
    kd.finish("abandoned", t0 + timedelta(hours=3))
    kc.merge({"history": kd.doc()["history"]})
    assert len(kc.conflicts) == 1 and len(kc.history) == 2
    keep = kc.conflicts[0][1]["id"]
    assert kc.resolve_conflict(keep) and [str(h["id"]) for h in kc.history] == [keep]
    # 正常情况: 别的设备结束了这个合约又开始了新的 (送来的记录里有本机的这个) → 不算冲突
    kf, kg = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    kf.start(["long1"], owner="F", now=t0)
    kg.merge(kf.doc())
    kg.finish("abandoned", t0 + timedelta(minutes=10))
    kg.start(["fog"], owner="G", now=t0 + timedelta(minutes=11))
    kf.merge(kg.doc())
    assert not kf.conflicts and str(kf.live["id"]) == str(kg.live["id"])
    # 不重叠的不算
    ke = Contract(tempfile.mkdtemp())
    ke.merge({"history": [dict(kd.history[0], id=999, start=_iso(t0 + timedelta(days=1)), end=_iso(t0 + timedelta(days=1, hours=1)))]})
    ke.merge({"history": [dict(kd.history[0], id=998, start=_iso(t0 + timedelta(days=2)), end=_iso(t0 + timedelta(days=2, hours=1)))]})
    assert not ke.conflicts

    # 网络禁令: 点击解除, 扣词条等级那么多目标生命值, 词条去掉 (组合算成员失效)
    cb = Contract(tempfile.mkdtemp())
    cb.start(["music", "silent", "game", "video"], owner="me", now=t0)
    assert cb.net_bans() == ["music", "silent", "game", "video"]
    assert "steam.exe" in cb.banned_exes() and "wechat.exe" in cb.banned_exes() and "cloudmusic.exe" in cb.banned_exes()
    assert cb.lift_cost("silent") == 2 and cb.lift_cost("music") == 1
    assert {"bilibili.com", "zhihu.com", "claude.ai", "music.163.com", "steampowered.com"} <= cb.banned_domains()
    assert cb.lift_ban("silent") == (False, 2) and cb.life == cb.violation_limit - 2
    assert not cb.has("silent") and "silent" not in cb.net_bans() and "wechat.exe" not in cb.banned_exes()
    assert "zhihu.com" not in cb.banned_domains() and "bilibili.com" in cb.banned_domains()
    assert dict((x["id"], s) for x, s in cb.combos())["nofun"] == "void" and not cb.echo("silent")
    assert cb.lift_ban("silent") == (False, 0), "已经解除了"
    assert cb.lift_ban("music") == (False, 1) and cb.life == 1
    assert cb.lift_ban("game") == (True, 1), "目标生命值归零"
    c.start(["silent", "game", "gaze"], owner="me", now=t0)
    assert c.level == 2 + 1 + 1 and c.records() == ["camera"] and c.ban_check("music", True) == ""
    assert c.ban_check("game", True) == "game" and c.ban_check("game", True) == "" and c.ban_check("silent", True) == "silent"
    assert c.violate("gaze") == (False, "") and c.records() == [] and c.level == 3
    assert c.violate("game") == (False, "") and not c.has("game") and c.has("silent")
    c.start(["silent"], owner="me", now=t0)
    assert c.term_level("silent") == 2 and c.level == 2
    assert c.violate("silent") == (False, "silent") and c.has("silent") and c.term_level("silent") == 1
    assert c.level == 1
    assert c.violate("silent") == (False, "") and not c.has("silent") and c.lost == ["silent"]
    # 互联网娱乐的败退 (词条组合): 四类都禁止, 每一类各自只在打开的那一下算; 开始时休息 +20 分钟
    c.start(["music", "silent", "game", "video"], owner="me", now=t0)
    assert c.level == 1 + 2 + 1 + 1 and c.ban_term("game") == "game" and c.ban_check("video", True) == "video"
    assert c.ban_check("video", True) == "" and rest_gift(c.terms) == 20 * 60
    # 凑齐的组合: 成员失效 (降级不算) 后组合的效果全部取消, 开始时给的休息时间不收回
    assert c.combos()[1] == (nofun, "done") and c.combo_marks().get("music") == COMBO_COLORS["purple"]
    assert c.violate("game") == (False, "") and c.combos()[1] == (nofun, "void")
    assert c.combo_marks() == {} and rest_gift(c.terms) == 0
    # 信息隔绝组合: 额外 1 危机等级, 完成时 +10 分钟; 成员失效后都没了
    c.start(iso, owner="me", now=t0)
    assert c.combos()[3] == (isolation, "done") and c.level == 8 + 1
    assert c.reward == rest_minutes(9) * 60 + 10 * 60 and c.combo_marks().get("fog") == COMBO_COLORS["red"]
    assert c.violate("suppress") == (False, "") and c.combos()[3] == (isolation, "void")
    assert c.level == 7 and c.reward == rest_minutes(7) * 60
    # 打年糕组合: 额外 1 危机等级, 休息时间 ×1.2; 纵深推进降级不算失效
    c.start(["long1", "tired2", "fine", "deep2"], owner="me", now=t0)
    assert c.combos()[0] == (mochi, "done") and c.level == 2 + 1 + 2 + 2 + 1
    assert abs(c.rest_factor() - 0.6 * 1.1 * 1.2) < 1e-9
    assert c.violate("deep2") == (False, "deep1") and c.combos()[0] == (mochi, "done")
    assert abs(c.rest_factor() - 0.6 * 1.1 * 1.2) < 1e-9, "降级不算失效"
    assert c.violate("deep1") == (False, "") and c.combos()[0] == (mochi, "void")
    assert abs(c.rest_factor() - 0.6 * 1.1) < 1e-9, "打年糕乘区取消, 持久战的 ×1.1 还在"
    # 翻页: 词条两页, 第 3 页是组合效果
    assert PAGES == [(0, 10), (10, BOARD_COLS)] and BOARD_COLS == 20 and LAYOUT["unknown"] == (19, 3)
    assert COMBO_PAGE == len(PAGES) and len(COMBOS) == 4
    # 全频道阻塞干扰: 合约期间一律换成 代理-全屏蔽 (proxy block-all), 结束回到设置里的断网方式
    c.start(["jam"], owner="me", now=t0)
    assert c.overrides() == {"NET_MODE": "proxy_all"}
    assert c.overrides() == {"NET_MODE": "proxy_all"}
    # 禁止囤囤鼠!: 提前结束休息算违反 (软规则, 1 级, 违反一次失效), 休息时间 ×1.1 (和疲惫相乘); 侵蚀: 冷静期 ×2 / ×3
    c.start(["hoard", "tired1", "erode2"], owner="me", now=t0)
    assert c.hoard_check() == "hoard" and abs(c.rest_factor() - 0.7 * 1.1) < 1e-9 and c.cooldown_factor() == 3.0
    assert c.violate("hoard") == (False, "") and not c.has("hoard") and abs(c.rest_factor() - 0.7) < 1e-9
    assert c.hoard_check() == "hoard" and c.echo("hoard"), "失效后再提前结束休息: 只响警报"
    c.start(["erode1"], owner="me", now=t0)
    assert c.hoard_check() == "" and c.cooldown_factor() == 2.0 and c.focus_check(60) == "" and c.quiet_check(True) == ""
    # 纵深推进: 专注不到 40 / 80 分钟算违反; -II 降成 -I
    c.start(["deep2", "quiet"], owner="me", now=t0)
    assert c.focus_check(79 * 60) == "deep2" and c.focus_check(80 * 60) == ""
    assert c.violate("deep2") == (False, "deep1") and c.focus_check(59 * 60) == "" and c.focus_check(39 * 60) == "deep1"
    # 静默协议: 专注中联网, 只在刚联网的那一下算; 1 级, 违反一次失效
    assert c.quiet_check(True) == "quiet" and c.quiet_check(True) == "" and c.quiet_check(False) == ""
    assert c.violate("quiet") == (False, "") and not c.has("quiet") and c.level == 1
    # 未知变量: 刚开始和之后每个小时开始时各选一个 1-2 级的词条 (不选录像, 不选冲突的), 不算进危机等级
    import random as _r
    c.start(["unknown", "info2", "long1"], owner="me", now=t0, pre=600)
    pool = c.random_candidates()
    assert "gaze" not in pool and "scar" not in pool and "info1" not in pool and "long2" not in pool
    assert "move" not in pool and "confine" not in pool, "没选目光: 不选高机动 / 禁足"
    assert not {"long1", "long2", "long3"} & set(pool), "不选持久战"
    assert "unknown" not in pool and "music" in pool and "jam" in pool

    class _Rng:
        """测试用: random() 依次给出 rs 里的数 (用完了给 0 = 抽词条), choice 按 pick 选 (没有时随机)."""
        def __init__(self, rs=(), pick=None):
            self.rs, self.pick, self.r = list(rs), pick, _r.Random(1)

        def random(self):
            return self.rs.pop(0) if self.rs else 0.0

        def choice(self, pool):
            return self.pick(pool) if self.pick else self.r.choice(pool)

    def _terms(ev):
        return [v for k, v in ev if k == "term"]
    rng = _Rng()
    first = _terms(c.roll(t0, rng))
    assert len(first) == 1 and c.rand == [] and list(c.waiting) == first, "抽中后还没生效"
    assert c.waiting[first[0]] == (t0 + timedelta(minutes=3), t0 + timedelta(hours=1)) and first[0] not in c.random_candidates()
    assert c.wake(t0 + timedelta(minutes=2, seconds=59)) == [] and not c.has(first[0])
    assert c.wake(t0 + timedelta(minutes=3)) == first and c.rand == first and c.waiting == {} and c.level == 3 + 2 + 1
    assert c.roll(t0 + timedelta(minutes=59), rng) == []
    assert c.rand_until(first[0]) == t0 + timedelta(hours=1), "只在抽中的那个小时里有效"
    more = _terms(c.roll(t0 + timedelta(hours=2, minutes=1), rng))
    assert len(more) == 1 and c.rand == [] and list(c.waiting) == more, "错过的小时补上; 同时只有一个"
    assert c.wake(t0 + timedelta(hours=2, minutes=3)) == more and c.rand == more and c.level == 6
    assert c.rand_until(more[0]) == t0 + timedelta(hours=3) and c.violations == 0, "到时移除不算违反"
    assert all(1 <= BY_ID[t][2] <= 2 and t in c.terms for t in c.rand)
    # 点击关闭: 算一次违反, 直接去掉 (不是降级)
    t = c.rand[0]
    assert c.violate(t, drop=True) == (False, "") and t not in c.terms and t not in c.rand and c.violations == 1
    # 下一个小时开始时再选一个, 这个小时结束时移除
    nxt = _terms(c.roll(t0 + timedelta(hours=3), rng))
    assert c.wake(t0 + timedelta(hours=3, minutes=5)) == nxt and c.rand == nxt
    assert c.expire(t0 + timedelta(hours=4)) == nxt and c.rand == []
    # 还没生效就到了小时结束 (程序关着): 直接去掉, 不再生效
    c.live["rand_wait"] = {"music": [_iso(t0 + timedelta(hours=4, minutes=3)), _iso(t0 + timedelta(hours=5))]}
    assert c.expire(t0 + timedelta(hours=5)) == [] and c.waiting == {} and c.wake(t0 + timedelta(hours=5)) == []
    assert c.violations == 1 and c.level == 6 and c.terms == ["long1", "info2", "unknown"], c.terms
    # 选中的词条降级: 还是它选中的, 移除的时刻不变
    c.start(["unknown"], owner="me", now=t0)
    c._enable("chan2", t0 + timedelta(hours=1))
    assert c.violate("chan2") == (False, "chan1") and c.rand == ["chan1"] and c.rand_until("chan1") == t0 + timedelta(hours=1)
    assert c.expire(t0 + timedelta(hours=1)) == ["chan1"] and c.terms == ["unknown"]
    # 选中支援短缺: 补上能用多少
    c.start(["unknown"], owner="me", now=t0, pre=600)
    c._enable("short1")
    assert c.supply_limit == 300 and c.rand == ["short1"] and c.rand_until("short1") is None
    # 退路封锁: 目标生命值减少后不为 0 才选; 只在选中的那个小时里有效, 小时结束时移除 (不算违反)
    c.violate("")
    c.violate("")
    assert "retreat2" not in c.random_candidates() and "retreat1" in c.random_candidates()
    c.live["rand_n"] = 1

    _Pick = _Rng(pick=lambda pool: "retreat1" if "retreat1" in pool else pool[0])
    assert c.roll(t0 + timedelta(hours=1, minutes=2), _Pick) == [("term", "retreat1")] and c.violation_limit == 4
    assert c.wake(t0 + timedelta(hours=1, minutes=5)) == ["retreat1"]
    assert c.violation_limit == 3 and c.life == 1 and c.rand_until("retreat1") == t0 + timedelta(hours=2)
    assert c.expire(t0 + timedelta(hours=1, minutes=59)) == [] and c.has("retreat1")
    assert c.expire(t0 + timedelta(hours=2)) == ["retreat1"] and not c.has("retreat1") and c.life == 2
    assert c.violations == 2 and c.rand == ["short1"]
    # 选过的不再选
    assert "retreat1" not in c.random_candidates() and "retreat2" not in c.random_candidates()
    rec = c.finish("abandoned", t0 + timedelta(hours=1))
    assert rec["rand"][0] == "short1"
    # 通过违反失效的词条可以再回到词条池, 但只会再被选中一次 (开始时选的, 和未知变量选中的都是)
    c.start(["unknown", "music"], owner="me", now=t0)
    assert "music" not in c.random_candidates() and c.violate("music") == (False, "")
    assert "music" in c.random_candidates(), "失效了: 回到词条池"
    pick_music = _Rng(pick=lambda pool: "music" if "music" in pool else pool[0])
    assert c.roll(t0, pick_music) == [("term", "music")] and c.wake(t0 + timedelta(minutes=3)) == ["music"]
    assert c.violate("music") == (False, "") and "music" not in c.random_candidates(), "只会再选中一次"
    c._pick("game", t0)
    assert c.wake(t0 + timedelta(minutes=3)) == ["game"] and "game" not in c.random_candidates()
    assert c.violate("game", drop=True) == (False, "") and "game" in c.random_candidates(), "关闭也算违反: 回到词条池"
    c._pick("game", t0)
    assert "game" not in c.random_candidates() and c.live["rand_again"] == ["music", "game"]
    # 有能选的词条: 75% 词条, 25% 效果; 效果按 15/15/15/15/10/30
    assert [Contract._effect(_Rng([x])) for x in (0.0, 0.149, 0.15, 0.3, 0.45, 0.6, 0.7, 0.999)] == \
        ["rest_cut", "rest_cut", "lock", "rest_add", "double", "slow", "none", "none"]
    c.start(["unknown"], owner="me", now=t0)
    assert c.roll(t0, _Rng([0.75, 0.0])) == [("rest_cut", "")] and c.waiting == {}, "0.75 及以上: 效果"
    assert c.roll(t0 + timedelta(hours=1), _Rng([0.7499])) and len(c.waiting) == 1
    # 再判定两次: 这次不生效, 改为判定两次 (这里: 一次词条, 一次 +10 分钟)
    ev = c.roll(t0 + timedelta(hours=2), _Rng([0.9, 0.45, 0.0, 0.9, 0.3]))
    assert [k for k, _v in ev] == ["double", "term", "rest_add"] and len(c.waiting) == 1, ev
    # 没有能选的词条: 一定是效果
    c.live["rand_used"] = [t[0] for t in TERMS]
    assert c.random_candidates() == [] and c.roll(t0 + timedelta(hours=3), _Rng([0.7])) == [("none", "")]
    # 阻滞: 只减慢合约内那部分专注 (合约结束后恢复正常速度); 合约结束后才开始的专注不生效
    c.live["rand_n"] = 3                       # 合约只有 4 小时: 再判定一次第 4 个小时
    assert c.roll(t0 + timedelta(hours=3, minutes=30), _Rng([0.6])) == [("slow", "")] and c.slow_pending
    late = t0 + timedelta(hours=4, minutes=10)
    assert not c.slow_bind(late) and c.slow_pending, "合约结束后才开始的专注不落上阻滞"
    s1 = t0 + timedelta(hours=3, minutes=30)
    assert c.slow_bind(s1) and not c.slow_pending and not c.slow_bind(s1)
    assert c.focus_rate(s1, s1) == 0.9 and c.focus_rate(s1, late) == 1.0, "合约结束后不再减慢"
    # 这段专注: 合约内 30 分钟按 90% 算, 合约结束后的 30 分钟按正常速度
    assert abs(c.focus_minutes(s1, 3600) - (1800 * 0.9 + 1800) / 60) < 1e-6
    # 封锁一个过了填写时限, 还没被封锁的记录格 (最近 24 小时里的); 填了的也锁
    fill = timedelta(hours=1)
    now = datetime(2026, 10, 18, 12, 10)
    texts = {"2026-10-18/10": "学习"}
    only10 = _Rng(pick=lambda pool: next(x for x in pool if x[0] == "2026-10-18/10"))
    assert c.lock_cell(now, only10, texts.get, "Sleeping", fill) == "2026-10-18/10"
    a, b = datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 11)
    assert c.hour_locked(a, b, "学习", now, fill, "Sleeping")
    assert not c.hour_locked(b, b + timedelta(hours=1), "", now, fill, "Sleeping"), "11 点那格还没过填写时限"
    last = _Rng(pick=lambda pool: pool[-1])
    assert c.lock_cell(now, last, texts.get, "Sleeping", fill) == "2026-10-18/9", "已经封锁的不再选"
    assert c.lock_cell(now, None, None, "Sleeping", fill) == ""
    # 记录封锁: 每次完成填写时另外封锁两个; 没选记录封锁时不封. 只封合约期间内的格子 (不会多出昨天 / 合约外的, #7)
    assert c.seal_extra(now, None, texts.get, "Sleeping", fill) == [], "没选记录封锁"
    c.live["terms"] = list(c.terms) + ["seal"]
    n0 = len(c.locks())
    now2 = datetime(2026, 10, 18, 14, 0)          # 合约 09:30–13:30 内, 9 / 10 点已锁, 还剩 11 / 12 点可封
    got = c.seal_extra(now2, None, texts.get, "Sleeping", fill)
    assert len(got) == 2 and len(set(got)) == 2 and len(c.locks()) == n0 + 2, got
    assert all(k in ("2026-10-18/11", "2026-10-18/12") for k in got), got
    c.live["terms"] = [t for t in c.terms if t != "seal"]
    c.finish("abandoned", now)
    # 合约提前结束了: 另外封锁的格子解除; 之后才开始的这段专注不减速 (阻滞只对合约内的专注生效)
    assert not c.hour_locked(a, b, "学习", now + timedelta(days=3), fill, "Sleeping"), "合约结束后解除封锁"
    assert c.focus_rate(s1) == 1.0 and abs(c.focus_minutes(s1, 3600) - 60) < 1e-6
    # 未知变量启用了整点记录的词条: 这次合约中一直强制开启整点记录
    c.start(["unknown"], owner="me", now=t0)
    assert not c.hour_forced()
    c._enable("seal", t0 + timedelta(hours=1))
    assert c.hour_forced() and c.expire(t0 + timedelta(hours=1)) == ["seal"] and c.hour_forced()
    c.finish("abandoned", t0 + timedelta(hours=2))
    assert not c.hour_forced()
    # 积攒的休息时间减少: 合约开始前的那部分最多剩这么多
    c.start(["unknown"], owner="me", now=t0, pre=600)
    c.pre_cap(900)
    assert c.pre_left == 600
    c.pre_cap(250)
    assert c.pre_left == 250
    c = Contract(tempfile.mkdtemp())          # 上面封锁的记录格会影响后面整点记录的测试
    # 打年糕: 休息时间额外 ×1.2 (和疲惫相乘); 成员降级不算失效, 失效后乘区取消
    c.start(["long1", "tired2", "fine", "deep2"], owner="me", now=t0)
    assert abs(c.rest_factor() - 0.6 * 1.1 * 1.2) < 1e-9 and abs(c.book_rest(1000, t0) - 792) < 1e-6
    assert c.level == 8 and c.combo_marks().get("deep2") == COMBO_COLORS["white"]
    c.downgrade("deep2")
    assert abs(c.rest_factor() - 0.6 * 1.1 * 1.2) < 1e-9, "降级不算失效"
    c.downgrade("deep1")
    assert abs(c.rest_factor() - 0.6 * 1.1) < 1e-9 and c.level == 5 and c.combo_marks() == {}
    # 没有疲惫: 基础 ×0.8 (打年糕照样相乘)
    assert rest_factor_of([]) == 0.8
    assert abs(rest_factor_of(["long1", "tired2", "fine", "deep1"]) - 0.6 * 1.1 * 1.2) < 1e-9
    c.start(["scar"], owner="me", now=t0)
    assert c.records() == ["screen"] and c.level == 1 and c.violate("scar") == (False, "") and c.terms == []
    # 目光: 只由开始时选定的设备 (cam) 录; 没有 cam 的 (旧版本) 各台电脑都录. 前置失效了高机动 / 禁足照样在
    c.start(["gaze", "scar", "move"], owner="me", now=t0, cam="phone")
    assert c.cam == "phone" and c.records("pc") == ["screen"] and c.records("phone") == ["camera", "screen"]
    assert c.violate("gaze") == (False, "") and c.terms == ["scar", "move"]
    c.start(["move", "scar"], owner="me", now=t0, cam="pc")
    assert c.terms == ["scar"] and c.cam == "" and c.records("x") == ["screen"], "没选目光: 不记 cam, 高机动去掉"
    c.start(["gaze"], owner="me", now=t0)
    assert c.cam == "" and c.records("pc") == ["camera"]
    c.start(["tired2", "chan1", "long1"], owner="me", now=t0)
    assert c.quick_check() == ""
    # 休息: 疲惫 ×0.6 × 持久战 long1 ×1.1 = 0.66; 完成时按降级后的等级给固定的时间 (等级 4 → 27 分钟)
    assert abs(c.book_rest(1000, t0 + timedelta(hours=1)) - 660) < 1e-6
    assert c.level == 4 and c.reward == 24 * 60
    # 空闲 15 分钟结束
    assert c.due(t0 + timedelta(hours=1), True) == ("", None)
    assert c.idle_left(t0 + timedelta(hours=1, minutes=10)) == 5 * 60
    # 空闲和设备离线共用 15 分钟: 空闲 10 分钟后有设备离线, 再过 5 分钟就失败 (从空闲开始算)
    assert c.offline_due([("手机", t0 + timedelta(hours=1, minutes=10))], t0 + timedelta(hours=1, minutes=14), t0) is None
    assert c.offline_due([("手机", t0 + timedelta(hours=1, minutes=10))], t0 + timedelta(hours=1, minutes=15), t0) ==         ("手机", t0 + timedelta(hours=1, minutes=15))
    assert c.due(t0 + timedelta(hours=1, minutes=16), True) == ("idle", t0 + timedelta(hours=1, minutes=15))
    assert c.due(t0 + timedelta(hours=1, minutes=16), False)[0] == "", "开始专注就不算空闲"
    # 设备离线 15 分钟失败: 从合约开始 / 本机打开程序之后才算
    t1 = t0 + timedelta(hours=1)
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + timedelta(minutes=14), t0) is None
    assert c.offline_due([("手机", t0 - timedelta(hours=3))], t0 + timedelta(minutes=16), t0) == \
        ("手机", t0 + timedelta(minutes=15))
    assert c.offline_due([("手机", t0)], t1 + timedelta(minutes=10), t1) is None, "本机刚打开"
    # 目标生命值: 默认 4; 退路封锁-I 只有 3, 违反 3 次结束; 整点记录超时未填写也算 (不降词条)
    c.start(["long1"], owner="me", now=t0)
    assert c.violation_limit == 4 and c.life == 4
    c.start(["suppress", "info3", "long1", "retreat1"], owner="me", now=t0)
    assert c.violation_limit == 3 and c.life == 3 and c.quick_check() == "suppress"
    assert c.violate("suppress") == (False, "") and not c.has("suppress") and c.life == 2
    assert c.violate("") == (False, "") and c.violations == 2 and c.level == 3 + 1 + 1 and c.life == 1
    assert c.violate("info3") == (True, "info2") and c.life == 0
    rec = c.finish("failed", t0 + timedelta(hours=2))
    assert rec["reward"] == 0 and rec["orig_level"] == 6 and rec["level"] == 4 and not c.active
    assert not c.take_first_break()

    # 违反到失效的词条照样检查: 再次触发返回它 (echo, 只响警报), 不扣目标生命值
    c.start(["chan1", "deep1", "quiet", "info1", "short1"], owner="me", now=t0, pre=600)
    assert c.window_check(5) == "chan1" and c.violate("chan1") == (False, "")
    assert c.window_watch() == ("chan1", 4) and c.window_check(5) == "" and c.window_check(3) == ""
    assert c.window_check(5) == "chan1" and c.echo("chan1"), "失效后按失效前的上限, 越过的那一下再算"
    c.window_reset()                          # 离开专注时段: 下次专注重新算
    assert c.window_check(5) == "chan1"
    assert c.violate("deep1") == (False, "") and c.focus_check(39 * 60) == "deep1" and c.focus_check(40 * 60) == ""
    assert c.quiet_check(True) == "quiet" and c.violate("quiet") == (False, "") and c.quiet_check(True) == ""
    assert c.quiet_check(False) == "" and c.quiet_check(True) == "quiet" and c.echo("quiet")
    assert c.violations == 3 and c.life == 1
    c.live["violations"] = 0                  # 目标生命值只有 4: 接着测剩下的
    assert c.net_check(0, 0, 4000, "d") == ("", "") and c.net_check(3300, 0, 0, "d") == ("info1", "over")
    assert c.violate("info1") == (False, "") and c.net_term() == "" and c.net_check(3300, 0, 0, "d") == ("", ""), "没有继续用"
    assert c.net_check(3400, 0, 0, "d") == ("info1", "over") and c.net_check(3400, 0, 0, "d") == ("", "")
    assert c.net_check(3400, 300, 0, "d") == ("info1", "loan") and c.echo("info1")
    assert c.supply_check(0, 400) == "short1" and c.violate("short1") == (False, "") and c.supply_limit is None
    assert c.supply_watch() == "short1" and c.supply_check(0, 400) == "" and c.supply_check(0, 400.3) == ""
    assert c.supply_check(0, 410) == "short1" and c.echo("short1")
    assert c.violations == 2 and c.lost == ["chan1", "deep1", "quiet", "info1", "short1"] and c.level == 0
    # 降级 (还没失效) / 关闭未知变量选中的: 不算失效
    c.start(["chan2", "unknown"], owner="me", now=t0)
    c._enable("music", t0 + timedelta(hours=1))
    assert c.violate("chan2") == (False, "chan1") and c.violate("music", drop=True) == (False, "") and c.lost == []
    assert c.ban_term("music") == "" and not c.echo("chan2")
    # 未知变量可以再选中已经失效的词条: 选中期间违反照常扣目标生命值, 移除后又只响警报
    c.start(["unknown", "music", "chan1"], owner="me", now=t0)
    assert c.violate("music") == (False, "") and c.violate("chan1") == (False, "") and c.lost == ["music", "chan1"]
    pool = c.random_candidates()
    assert "music" in pool and "chan1" in pool and "chan2" in pool
    c._enable("music", t0 + timedelta(hours=1))
    assert not c.echo("music") and c.ban_check("music", True) == "music"
    assert c.violate("music") == (False, "") and c.violations == 3 and c.lost == ["music", "chan1"] and c.echo("music")
    c._enable("chan2", t0 + timedelta(hours=1))
    assert c.window_watch() == ("chan2", 3) and not c.echo("chan2")
    assert c.expire(t0 + timedelta(hours=1)) == ["chan2"] and c.window_watch() == ("chan1", 4) and c.echo("chan1")

    # 最后防线: 只有初始目标生命值 1, 没有额外的词条等级
    c.start(["last", "long3", "tired3", "chan1"], owner="me", now=t0)
    assert c.level == 10 and c.orig_level == 10 and c.violation_limit == 1
    assert c.violate("chan1") == (True, "") and c.level == 9   # 目标生命值只有 1

    # 完成: 按等级给固定的休息时间 (等级 4 → 27 分钟), 第一次休息的结转比例
    c.start(["last", "tired1"], owner="me", now=t0)
    assert c.violation_limit == 1 and abs(c.book_rest(600, t0) - 420) < 1e-6 and c.reward == 24 * 60
    assert c.due(t0 + timedelta(hours=4), False) == ("completed", t0 + timedelta(hours=4))
    rec = c.finish("completed", t0 + timedelta(hours=4, minutes=3))
    assert rec["reward"] == 24 * 60 and rec["end"] == (t0 + timedelta(hours=4)).isoformat()
    assert abs(c.book_rest_after(600, t0 + timedelta(hours=3)) - 420) < 1e-6 and c.reward == 0, "完成后才结束的专注只乘疲惫"
    assert c.book_rest_after(600, t0 + timedelta(hours=5)) == 600
    assert c.take_first_break() and not c.take_first_break()
    assert abs(Contract.first_break_rate(0.7) - 0.85) < 1e-9

    # 最近结束的那个, 不是最近开始的那个
    c.start(["long1"], now=t0 - timedelta(days=1))
    c.finish("abandoned", t0 - timedelta(days=1) + timedelta(hours=1))
    assert c.last["result"] == "completed" and abs(c.book_rest_after(600, t0 + timedelta(hours=3)) - 420) < 1e-6

    # 合约开始前积攒的休息时间 (20 分钟) + 支援短缺-II: 一点都不能用
    c.start(["short2"], owner="me", now=t0, pre=1200)
    assert c.pre == 1200 and c.pre_left == 1200 and c.supply_limit == 0
    taken = c.pre_take(1500)                 # 积攒的 25 分钟里有 20 分钟是合约开始前的
    assert taken == 1200 and c.pre_left == 0
    # 这次休息 = 建议 10 分钟 + 积攒 25 分钟; 剩余 > 20 分钟时还没用到合约开始前的
    assert c.supply_until(1800, taken) == 600 and c.supply_check(1300, taken) == ""
    assert c.supply_check(1100, taken) == "short2"
    c.pre_settle(taken, 1100)                # 暂停: 合约开始前的 1100 秒原样还回去
    assert c.pre_left == 1100 and abs(c.pre_used - 100) < 1e-6
    assert c.violate("short2") == (False, "short1") and c.supply_limit == 600, "降级: 多给 50%"
    assert c.supply_check(0, 0) == "" and c.supply_until(1100, 1100) == 1100 - (1100 - 500)
    rec = c.finish("abandoned", t0 + timedelta(hours=1))
    assert rec["pre_left"] == 1100

    # 整点记录: 超时未填写算违反 (每格一次); 细化规范分成半小时; 记录封锁只在合约中有效
    fill = timedelta(hours=1)
    texts = {"2026-10-18/9/2": "学习"}
    c.start(["seal", "fine"], owner="me", now=t0)
    assert c.hour_forced() and c.fine_hour(datetime(2026, 10, 18, 9)) and not c.fine_hour(datetime(2026, 10, 18, 8))
    units = c.hour_units(datetime(2026, 10, 18, 10, 40))
    assert [u[0] for u in units] == ["2026-10-18/9/2", "2026-10-18/10/1"], units
    # 细化规范的半小时格补填时限只有 30 min (10:00-10:30 那格 11:00 到期)
    assert c.hour_misses(datetime(2026, 10, 18, 10, 59), fill, texts.get, "Sleeping") == []
    assert c.hour_misses(datetime(2026, 10, 18, 11, 1), fill, texts.get, "Sleeping") == ["2026-10-18/10/1"]
    assert c.hour_misses(datetime(2026, 10, 18, 11, 10), fill, texts.get, "Sleeping") == [], "每格只算一次"
    a, b = datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 10, 30)
    assert not c.hour_locked(a, b, "", datetime(2026, 10, 18, 10, 59), fill, "Sleeping")
    assert c.hour_locked(a, b, "Sleeping", datetime(2026, 10, 18, 11, 1), fill, "Sleeping")
    assert c.hour_locked(a, b, "学习", datetime(2026, 10, 18, 11, 1), fill, "Sleeping"), "过了时限, 填了的也锁"
    assert c.hour_locked(a, b, "学习", datetime(2026, 10, 18, 10, 59), fill, "Sleeping"), "填写了就不能再改 (时限之内也一样)"
    assert not c.hour_locked(a, b, "", datetime(2026, 10, 18, 11, 30), fill, "Sleeping"), "没填的照常能填"
    c.finish("abandoned", datetime(2026, 10, 18, 12))
    assert not c.hour_locked(a, b, "Sleeping", datetime(2026, 10, 19), fill, "Sleeping"), "合约结束后恢复"
    assert not c.hour_sealed(a, b, datetime(2026, 10, 19)), "合约结束后不用确认"
    nos = Contract(tempfile.mkdtemp())
    nos.start(["long1"], owner="me", now=t0)
    assert not nos.hour_sealed(a, b, datetime(2026, 10, 18, 11)), "没选记录封锁: 不用确认"
    assert not nos.hour_locked(a, b, "学习", datetime(2026, 10, 18, 11), fill, "Sleeping"), "没选记录封锁: 不锁"
    assert nos.seal_extra(datetime(2026, 10, 18, 11), None, texts.get, "Sleeping", fill) == [], "没选记录封锁: 不另外封锁"
    assert not c.hour_locked(datetime(2026, 10, 18, 13), datetime(2026, 10, 18, 14), "", datetime(2026, 10, 19),
                             fill, "Sleeping")

    # 黑雾: 整点记录的假内容开始合约时就抽好, 跟着合约同步, 两端一样
    fa, fb = Contract(tempfile.mkdtemp()), Contract(tempfile.mkdtemp())
    fa.start(["fog"], "A")
    w = fa.fog_words()
    assert len(w) == FOG_SLOTS and w == fa.live["fog_words"]
    assert all(len({x[k] for x in w}) == FOG_SLOTS for k in range(3)), "一次抽的词不重复"
    fb.merge({"live": fa.doc()["live"]})
    assert fb.fog_words() == w, "同步来的合约: 同一份"
    fa.live.pop("fog_words")
    assert len(fa.fog_words()) == FOG_SLOTS and fa.fog_words() == fa.fog_words(), "以前开始的合约: 按 id 补一份"

    # 同步: 记录按 id 合并
    d = Contract(tempfile.mkdtemp())
    d.merge(c.doc())
    assert len(d.history) == len(c.history) and not d.active
    d.merge({"history": c.doc()["history"]})
    assert len(d.history) == len(c.history) >= 3, "同一条记录不重复"
    c2 = Contract(os.path.dirname(c.path))
    assert len(c2.history) == len(c.history), "写进文件, 重开后仍在"
    return "contract selftest OK"


if __name__ == "__main__":
    print(_selftest())
