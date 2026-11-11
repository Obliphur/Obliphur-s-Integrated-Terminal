"""合约的纯规则 (MECHANICS.md 第 18 节): 词条表、组合、休息 / 等级 / 时长公式、黑雾词池. 不碰存档, 不碰 Contract 实例."""

import re
from datetime import datetime, timedelta


BASE_SECONDS = 3 * 3600          # 合约的初始时长
IDLE_LIMIT = 15 * 60             # 合约中空闲 (不在专注也不在休息) 累计超过这么久就结束 (累计, 不要求连续)
OFFLINE_LIMIT = IDLE_LIMIT       # 合约中有绑定的设备离线累计超过这么久就失败 (和空闲共用这 15 分钟, 重叠的部分只算一次)
VIOLATION_LIMIT = 4              # 初始目标生命值: 每违反一次减一, 减到 0 合约失败
HISTORY_KEEP = 30
FRESH_MS = 2 * 60 * 1000         # 合约的主人这么久没同步过: 本机来做自动结束

# (id, 组, 等级, 名字, 描述, 冲突的词条)
# 同一组的词条互相冲突 (系列的各档在同一列); 冲突是双向的 (这里只写一边).
# 顺序同词条板 (BOARD): 第 1 页 合约本身 / 专注 / 休息 / 整点记录, 第 2 页 联网 / 网络禁令 / 录像 / 现实 / 黑雾 / 未知变量.
TERMS = [
    # ==== 第 1 页 ====
    # ---- 合约本身: 时长, 目标生命值 ----
    ("long1", "long", 1, "环境:持久战-I", "时长改为120%, 结算休息时长×110%", ()),
    ("long2", "long", 2, "环境:持久战-II", "时长改为140%, 结算休息时长×120%", ()),
    ("long3", "long", 3, "环境:持久战-III", "时长改为160%, 结算休息时长×130%", ()),
    ("retreat1", "retreat", 1, "环境:退路封锁-I", "初始目标生命值为3", ()),
    ("retreat2", "retreat", 2, "环境:退路封锁-II", "初始目标生命值为2", ()),
    ("last", "retreat", 3, "最后防线", "初始目标生命值为1", ()),
    # ---- 专注: 专注的下限, 专注中的窗口数 ----
    ("deep1", "deep", 1, "目标:纵深推进-I", "专注的时间不能少于40min", ()),
    ("deep2", "deep", 2, "目标:纵深推进-II", "专注的时间不能少于80min", ()),
    ("chan1", "chan", 1, "环境:通道阻塞-I", "在专注期间的最大窗口数改为5", ()),
    ("chan2", "chan", 2, "环境:通道阻塞-II", "在专注期间的最大窗口数改为4", ()),
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
# 其余是硬规则, 立即生效 (记录封锁, 细化规范, 持久战, 疲惫).
# 录像 (目光 / 刻痕) 违反一次就失效 (录像停止): 不在 LOWER / SINGLE 里.
SOFT = {"info1", "info2", "info3", "chan1", "chan2", "music", "suppress", "short1", "short2",
        "silent", "game", "video", "gaze", "scar", "deep1", "deep2", "quiet", "hoard", "unknown", "fog", "jam"}
LOWER = {"info3": "info2", "info2": "info1", "chan2": "chan1",
         "short2": "short1", "deep2": "deep1"}   # 降一级换成哪个词条
SINGLE = {"music", "suppress", "silent", "game", "video", "quiet", "hoard", "jam"}   # 只有一档的软规则: 降级时等级减一, 减到 0 就失效

DURATION = {"long1": 1.2, "long2": 1.4, "long3": 1.6}
NET_SHARE = {"info1": 0.8, "info2": 0.65, "info3": 0.5}
BASE_REST = 0.8                                # 合约中专注换来的休息时间的基础倍率; 有疲惫时换成疲惫的倍率
REST = {"tired1": 0.7, "tired2": 0.6, "tired3": 0.5}
REST_BONUS = {"hoard": 1.1}                     # 休息时间额外的乘区 (和疲惫不冲突, 相乘): 禁止囤囤鼠! ×1.1
LONG_REWARD = {"long1": 1.1, "long2": 1.2, "long3": 1.3}   # 持久战: 完成合约的结算休息时长 ×110 / 120 / 130%
WINDOWS = {"chan1": 5, "chan2": 4}
SUPPLY = {"short1": 0.5, "short2": 0.0}         # 合约开始前积攒的休息时间能用几成
HOUR_TERMS = {"seal", "fine"}                   # 整点记录的词条: 整点记录关着时, 这次合约中照样开启
LIFE = {"retreat1": 3, "retreat2": 2, "last": 1}  # 初始目标生命值 (硬规则)
RECORD = {"gaze": "camera", "scar": "screen"}   # 录像的词条 → 录什么 (recorder.py)
# 网络禁令 (MECHANICS 18.3): 这一类软件合约期间禁止联网 (打开它们不算违反); 点击词条可以解除, 扣掉词条等级那么多目标生命值
BANS = ("music", "silent", "game", "video")
SUPPLY_WARN = (180, 60)                         # 支援短缺: 还差这么多秒就用超时各提醒一次
DEEP = {"deep1": 40 * 60, "deep2": 80 * 60}     # 纵深推进: 一段专注至少这么多秒, 不到就结束算违反
FOG_SWITCHES = 50                               # 黑雾 (软): 合约中切到计时器以外的页面满这么多次算一次违反, 每个钟点清零
JAM_BLOCKS = 20                                 # 全频道阻塞干扰 (软): 打开的程序被名单禁止满这么多次算一次违反, 每个钟点清零
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
# 0 级 20 分钟, 30 级起封顶 45 分钟; 中间每一级按直线均匀填充, 四舍五入到整分钟
REST_MIN, REST_MAX, REST_TOP = 20, 45, 30

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
    {"id": "mochi", "name": "打年糕", "color": "yellow",
     "reqs": ("tired", "long", "fine", "deep"),
     "level": 1, "rest": 1.2, "gift": 0, "end": 0,
     "desc": "额外 1 危机等级\n休息时间 ×120%"},
    {"id": "nofun", "name": "互联网娱乐的败退", "color": "purple",
     "reqs": ("music", "silent", "game", "video"),
     "level": 0, "rest": 1.0, "gift": 20 * 60, "end": 0,
     "desc": "合约开始时 休息 +20 分钟"},
    {"id": "flawless", "name": "无懈可击", "color": "blue",
     "reqs": ("last", "unknown", "move", "confine", "gaze", "scar"),
     "level": 0, "rest": 1.0, "gift": 20 * 60, "end": 0,
     "desc": "合约开始时 休息 +20 分钟"},
    {"id": "isolation", "name": "信息隔绝", "color": "red",
     "reqs": ("info", "chan", "erode", "suppress", "fog", "quiet", "jam"),
     "level": 1, "rest": 1.0, "gift": 0, "end": 10 * 60,
     "desc": "额外 1 危机等级\n完成合约时 休息 +10 分钟"},
]
COMBOS_BY_ID = {c["id"]: c for c in COMBOS}

# 合约主题 (MECHANICS 18.7): 起源 / 都市之意. 开始合约时用的主题记在合约里 (随同步), 完成合约时按它结算:
# 起源 + 无懈可击 凑齐着, 结算休息时间额外 +5 分钟 (和组合自己的"完成时给的"一样先加上, 再乘持久战); 都市之意没有加成.
# "" = 没有主题 (开始时没记, 例如旧版本开始的合约 / 手机上选了"跟随界面主题"): 没有这一项加成.
THEMES = {"ops": "起源", "urban": "都市之意"}
DEFAULT_THEME = "ops"
THEME_COMBO = {"ops": "flawless"}      # 主题 -> 绑定的组合 (没有的主题没有加成)
THEME_END = 5 * 60


def clean_theme(theme) -> str:
    theme = str(theme or "")
    return theme if theme in THEMES else ""


def theme_end(terms, theme="") -> int:
    """主题加成: 主题绑定的组合凑齐着时, 完成合约额外给的休息时间 (秒)."""
    cid = THEME_COMBO.get(clean_theme(theme))
    return THEME_END if cid and combo_done(COMBOS_BY_ID[cid], terms) else 0


def theme_note(combo, theme="") -> str:
    """组合栏里这个组合的主题加成说明 (这个组合不是主题绑定的就是 "")."""
    theme = clean_theme(theme)
    if theme and THEME_COMBO.get(theme) == combo["id"]:
        return f"{THEMES[theme]}: 完成合约时 休息额外 +{THEME_END // 60} 分钟"
    return ""
COMBO_COLORS = {"yellow": "#ffd23f", "purple": "#b06cf0", "blue": "#2456c7", "red": "#ff2b3a"}
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


def combo_end(terms, theme="") -> int:
    """完成合约时凑齐的组合额外给的休息时间 (秒), 加上主题加成 (theme_end)."""
    return int(sum(int(c["end"]) for c in COMBOS if combo_done(c, terms))) + theme_end(terms, theme)


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
    再乘额外的乘区 (禁止囤囤鼠! ×1.1, 打年糕 ×1.2)."""
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


def rest_reward(level: int, terms=None, theme="") -> int:
    """完成合约时存进积攒的休息时间的秒数 (按等级查表 + 凑齐的组合的结束后收益 + 主题加成, 再乘持久战的结算休息时长倍率)."""
    terms = terms or []
    base = rest_minutes(level) * 60 + combo_end(terms, theme)
    mult = next((LONG_REWARD[t] for t in terms if t in LONG_REWARD), 1.0)
    return int(round(base * mult))


CHECK_MIN, CHECK_MAX = 40, 60                  # 合约专注的"还在吗"间隔: 每次随机 40 ~ 60 分钟 (MECHANICS 6.6)


def _fnv(text: str) -> int:
    """FNV-1a 32 位: 各设备 (电脑 / 手机) 用同样的算法, 算出同样的间隔, 不用另外同步."""
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def check_mark(n: int, start: datetime, contract: bool, every: float) -> float:
    """第 n 次 (n >= 1) "还在吗"提醒在开始专注后多少秒.
    普通专注: 每 every 秒一次; 合约专注 (focus_tag 不空): 每次间隔 40 ~ 60 分钟里随机的整分钟,
    按开始专注的时刻 (精确到秒) 和第几次算, 同一段专注在各设备上一样."""
    if not contract or start is None:
        return every * n
    key = f"{start:%Y-%m-%dT%H:%M:%S}"
    span = CHECK_MAX - CHECK_MIN + 1
    return float(sum(60 * (CHECK_MIN + _fnv(f"{key}/{i}") % span) for i in range(1, n + 1)))


REST_SHARE_FROM = 0.3                          # k 不超过这个时一点不给
REST_SHARE_NEAR = 0.6                          # k 趋近 1 (还没完成) 时给到的比例; 完成 (k = 1) 才给全部


def rest_share(k: float) -> float:
    """合约结束时给出完成奖励的几成: k = 进行了多久 / 总时长.
    k <= 0.3: 0; 0.3 < k < 1: 0.6 * ((k - 0.3) / 0.7)² (f'' > 0, 越到后面涨得越快, k → 1 时趋近 0.6); k = 1: 1."""
    k = min(1.0, max(0.0, float(k)))
    if k >= 1.0:
        return 1.0
    if k <= REST_SHARE_FROM:
        return 0.0
    x = (k - REST_SHARE_FROM) / (1.0 - REST_SHARE_FROM)
    return REST_SHARE_NEAR * x * x


def rest_minutes_total(level: int, terms=None, theme="") -> int:
    """完成合约时休息时间一共多少分钟 (界面上写 "完成后休息 +N 分钟")."""
    return rest_reward(level, terms, theme) // 60


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


def record_rows(rec: dict) -> list:
    """统计页的合约记录: 和合约页 (pages/contract_page.py 的 _rows "合约进行中"分支) 一样地列出词条.
    返回 [(等级, 要显示的词条 id, 是否失效, 降级前的原词条 id 或 "")], 按开始时 (orig) 的顺序, 未知变量选中的放最后.
    降级的显示降成的那个 + 原词条; 失效的显示原词条并标失效; 等级取要显示的那个词条的级别."""
    rec = rec if isinstance(rec, dict) else {}
    rand = set(rec.get("rand") or [])
    mine = [t for t in (rec.get("terms") or []) if t in BY_ID and t not in rand]
    out = []
    for t in rec.get("orig") or []:
        if t not in BY_ID:
            continue
        now = next((x for x in mine if x == t or BY_ID[x][1] == BY_ID[t][1]), "")
        if not now:
            out.append((BY_ID[t][2], t, True, ""))            # 已失效: 显示原名
        elif now != t:
            out.append((BY_ID[now][2], now, False, t))        # 降级: 显示降成的, 标 [原 ...]
        else:
            out.append((BY_ID[t][2], t, False, ""))
    for t in rec.get("terms") or []:                          # 未知变量选中的放最后
        if t in BY_ID and t in rand:
            out.append((BY_ID[t][2], t, False, ""))
    # 完成时凑齐了、没有失效的组合: 成员放在一起置顶 (按组合的顺序), 其余照旧
    marks = record_marks(rec)
    order = {c["id"]: i for i, c in enumerate(COMBOS)}
    top = sorted([r for r in out if r[1] in marks and not r[2]], key=lambda r: order[marks[r[1]]])
    return top + [r for r in out if r not in top]


def record_marks(rec: dict) -> dict:
    """统计页的合约记录: 完成时凑齐了的组合 (没有失效的) 的成员词条 → 组合 id (界面上用组合的颜色写它们).
    按结束时的词条算, 未知变量选中的不算 (同合约中的组合)."""
    rec = rec if isinstance(rec, dict) else {}
    rand = set(rec.get("rand") or [])
    mine = [t for t in (rec.get("terms") or []) if t in BY_ID and t not in rand]
    out = {}
    for c in COMBOS:
        if not combo_done(c, mine):
            continue
        for t in mine:
            if any((BY_ID[t][1] == req) if req in GROUPS else (t == req) for req in c["reqs"]):
                out.setdefault(t, c["id"])
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
FOG_POOL = (125, 80, 150)


def _fog_words(rnd) -> list:
    cols = [rnd.sample(range(n), FOG_SLOTS) for n in FOG_POOL]
    return [[c[i] for c in cols] for i in range(FOG_SLOTS)]
