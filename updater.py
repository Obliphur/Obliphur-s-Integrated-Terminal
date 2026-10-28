"""
从 GitHub 检查 / 安装更新 (MECHANICS.md 第 16 节)
──────────────────────────────────────────────
    检查   GET https://api.github.com/repos/<REPO>/releases/latest,
           发布的版本号取自名称或标签里的数字 (如 "1.2.0", "v1.2"), 和 config.APP_VERSION 比较
    安装   发布里有 windows / pc / python 字样的 .zip 附件就用它, 否则用这个标签的源码压缩包;
           解压后把程序文件覆盖到程序文件夹 (不删除多出来的文件, 不碰数据目录), 重启后生效.
           程序文件夹是 git 仓库 (开发者) 时只提示, 不覆盖.

只连 GitHub 的地址, 只走 https, 跳转也只能跳到 GitHub 的下载地址.
不经过 OIT 自己的过滤代理, 所以 Proxy 断网方式下不管名单怎么写都能检查.
下载安装包走 GitHub API 的附件地址 (api.github.com/…/releases/assets/<id>, Accept: application/octet-stream),
它直接跳到 release-assets.githubusercontent.com —— 不经过 github.com: 有的网络里 github.com 连不上, api.github.com 和下载服务器却能直连.
下载完核对大小和 GitHub 给的 SHA-256 (附件的 digest). 源码包走 api.github.com → codeload.github.com.
直连不通时再经过用户原来的代理 (Clash 之类, 见 sysproxy_backup.json).
Adapter 方式离线时网卡是禁用的, 检查会失败, 下一次联网时再查.

    python updater.py       自检 (不联网)
"""

import hashlib
import json
import os
import re
import filecmp
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import config

REPO = "Obliphur/Obliphur-s-Integrated-Terminal"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ALLOWED_HOSTS = {"api.github.com", "github.com", "codeload.github.com",
                 "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_DOWNLOAD = 80 * 1024 * 1024
# 覆盖安装时跳过的东西: 安卓版, 版本库, 缓存
SKIP_DIRS = {"Android version", ".git", ".github", "__pycache__", ".vscode", ".idea"}


def parse_version(text):
    """'v1.2' / '1.2.0' / '1' → (1, 2, 0); 没有数字返回 None."""
    nums = re.findall(r"\d+", str(text or ""))
    if not nums:
        return None
    nums = [int(n) for n in nums[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def release_version(rel: dict):
    """发布的版本号: 名称里有就用名称的 (如标签 '1', 名称 '1.0.0'), 否则用标签的."""
    name, tag = rel.get("name") or "", rel.get("tag_name") or ""
    vn, vt = parse_version(name), parse_version(tag)
    if vn and re.search(r"\d+\.\d+", name):
        return vn
    return vt or vn


def vtext(v) -> str:
    return ".".join(str(x) for x in v) if v else "?"


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or (u.hostname or "").lower() not in ALLOWED_HOSTS:
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-GitHub address refused",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OIT_PORTS = range(17890, 17911)      # OIT 自己的过滤代理可能用的端口 (netproxy.server, 被占用时往后找)


def _backup_file() -> str:
    return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "AppData", "LocalLow",
                        config.APP_NAME, "sysproxy_backup.json")


def upstream_proxy():
    """用户自己的代理 (host, port): OIT 接管系统代理之前的设置; 没接管时就是现在的系统代理.
    OIT 自己的过滤代理不算. 没有返回 None."""
    try:
        from netproxy.sysproxy import SystemProxy, parse_proxy_server
        sp = SystemProxy(_backup_file())
        up = sp.original_upstream()
        if up is None and not os.path.exists(_backup_file()):
            cur = sp.backend.read()
            if cur.get("ProxyEnable") == 1:
                up = parse_proxy_server(cur.get("ProxyServer"))
    except Exception:
        return None
    if not up or (up[0] in ("127.0.0.1", "localhost") and up[1] in OIT_PORTS):
        return None
    return up


_route = {"via": None}                 # 上一次走通的路线: "proxy" / "direct"
ACCEPT_JSON = "application/vnd.github+json"
ACCEPT_FILE = "application/octet-stream"


def _routes():
    """直连在前 (走 API 附件地址就不用代理), 用户原来的代理在后; 上一次走通的那条排第一."""
    up = upstream_proxy()
    routes = [("direct", None), ("proxy", up)] if up else [("direct", None)]
    if _route["via"] == "proxy":
        routes.sort(key=lambda r: r[0] != "proxy")
    return routes


def _open(url: str, timeout: float, accept: str = ACCEPT_JSON):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError("only GitHub https addresses are allowed")
    req_headers = {"User-Agent": f"OIT/{config.APP_VERSION}", "Accept": accept}
    last = None
    for via, up in _routes():
        # 永远不走 OIT 自己的过滤代理; 直连不通再经过用户原来的代理
        proxies = {"http": f"http://{up[0]}:{up[1]}", "https": f"http://{up[0]}:{up[1]}"} if up else {}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), _CheckedRedirect())
        try:
            r = opener.open(urllib.request.Request(url, headers=req_headers), timeout=timeout)
            _route["via"] = via
            return r
        except urllib.error.HTTPError:
            raise                      # 服务器回了错误: 换路线也一样
        except OSError as e:           # 连不上 / 超时: 换下一条路线
            last = e
    raise last


def program_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def is_dev_checkout(target: str = None) -> bool:
    return os.path.isdir(os.path.join(target or program_dir(), ".git"))


def asset_source(a: dict) -> dict:
    """附件的下载来源: GitHub API 的附件地址 (不经过 github.com), 大小和 SHA-256 用来核对."""
    url = str(a.get("url") or "")
    if not url.startswith("https://api.github.com/"):
        url = a.get("browser_download_url")
    digest = str(a.get("digest") or "")
    return {"url": url, "accept": ACCEPT_FILE, "size": a.get("size"),
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}


def pick_download(rel: dict):
    """(下载来源, 是否是源码包). 优先用电脑版的 .zip 附件, 没有就用源码包 (api.github.com → codeload)."""
    for a in rel.get("assets") or []:
        n = str(a.get("name") or "").lower()
        if n.endswith(".zip") and any(k in n for k in ("windows", "win", "pc", "python")):
            return asset_source(a), False
    z = rel.get("zipball_url")
    return ({"url": z, "accept": ACCEPT_JSON, "size": None, "sha256": None} if z else None), True


def pick_apk(rel: dict):
    """发布里的安卓安装包的下载来源: 优先名字里带 release / android 的 .apk (和手机端 Updater.pickApk 相同)."""
    apks = [a for a in rel.get("assets") or [] if str(a.get("name") or "").lower().endswith(".apk")]
    best = next((a for a in apks if any(k in str(a.get("name")).lower() for k in ("release", "android"))),
                apks[0] if apks else None)
    return asset_source(best) if best else None


def package_source(rel: dict, platform: str):
    """给某个平台的设备用的安装包: windows → 电脑版 zip (没有就用源码包), android → apk."""
    if platform == "android":
        return pick_apk(rel)
    if platform == "windows":
        return pick_download(rel)[0]
    return None


def download(src, dest: str):
    """下载到 dest (先写临时文件, 核对过大小和 SHA-256 再改名; 失败时删掉临时文件).
    src: 下载来源 (pick_download / pick_apk 给的), 或者一个地址."""
    if isinstance(src, str):
        src = {"url": src, "accept": ACCEPT_JSON, "size": None, "sha256": None}
    tmp = dest + ".part"
    try:
        h = hashlib.sha256()
        total = 0
        with _open(src["url"], 60, src.get("accept") or ACCEPT_JSON) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError("download too large")
                h.update(chunk)
                f.write(chunk)
        if src.get("size") is not None and total != int(src["size"]):
            raise ValueError(f"size mismatch ({total} != {src['size']})")
        if src.get("sha256") and h.hexdigest() != src["sha256"]:
            raise ValueError("SHA-256 mismatch")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def install_zip(zip_path: str, target: str) -> int:
    """把压缩包里的程序文件覆盖到 target. 返回覆盖的文件数. 找不到程序文件时抛 ValueError.

    内容没变的文件不动 (运行中被占用的文件, 例如私有加载的字体, 覆盖会失败: WinError 32);
    有变化但被占用的资源文件跳过, 留到下一次; 被占用的 .py 才算失败."""
    tmp = tempfile.mkdtemp(prefix="oit-update-")
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                p = os.path.normpath(info.filename)
                if p.startswith("..") or os.path.isabs(p):
                    raise ValueError("unsafe path in archive")
            z.extractall(tmp)
        root = None
        for dirpath, dirnames, filenames in os.walk(tmp):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if "main.py" in filenames and "app.py" in filenames:
                root = dirpath
                break
        if root is None:
            raise ValueError("main.py / app.py not found in the archive")
        n = 0
        locked = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel = os.path.relpath(dirpath, root)
            dest_dir = target if rel == "." else os.path.join(target, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for f in filenames:
                if f.endswith((".pyc", ".pyo")):
                    continue
                src, dst = os.path.join(dirpath, f), os.path.join(dest_dir, f)
                if os.path.exists(dst) and _same_file(src, dst):
                    continue
                try:
                    shutil.copy2(src, dst)
                    n += 1
                except OSError as e:                  # 文件被别的程序 (包括 OIT 自己) 占用
                    if not f.endswith(".py"):
                        locked.append(dst)
                        continue
                    raise e
        return n
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Updater:
    """state: idle / checking / latest / available / downloading / installed / dev / error"""

    def __init__(self):
        self.state = "idle"
        self.current = parse_version(config.APP_VERSION)
        self.latest = None
        self.release = None
        self.error = ""
        self._lock = threading.Lock()

    @property
    def latest_text(self) -> str:
        return vtext(self.latest)

    def fetch_release(self) -> dict:
        """在当前线程里取 GitHub 上的最新发布 (发更新给其他设备时用), 顺便更新 latest / state."""
        with _open(API_LATEST, 20) as r:
            rel = json.loads(r.read(2_000_000).decode("utf-8"))
        v = release_version(rel)
        if not v:
            raise ValueError("no version number in the release")
        self.release, self.latest = rel, v
        if self.state in ("idle", "latest", "available", "error"):
            self.state = "available" if v > self.current else "latest"
        return rel

    def fetch_package(self, rel: dict, platform: str, cache_dir: str) -> str:
        """给其他设备下载这个平台的安装包, 存在 cache_dir 里 (同一个版本只下载一次). 返回文件路径."""
        src = package_source(rel, platform)
        if not src or not src.get("url"):
            raise ValueError("no-package")
        v = vtext(release_version(rel))
        ext = ".apk" if platform == "android" else ".zip"
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, f"OIT-{platform}-{v}{ext}")
        for f in os.listdir(cache_dir):                 # 旧版本的包删掉
            p = os.path.join(cache_dir, f)
            if p != dest and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if not (os.path.isfile(dest) and os.path.getsize(dest) > 0):
            download(src, dest)
        return dest

    def install_file(self, zip_path: str, done=None):
        """装其他设备直接发来的电脑版安装包 (已核对过 SHA-256); 完成后调用 done(self)."""
        if is_dev_checkout():
            self.state = "dev"
            if done:
                done(self)
            return
        self.state = "downloading"

        def run():
            try:
                install_zip(zip_path, program_dir())
                self.state, self.error = "installed", ""
            except Exception as e:      # noqa: BLE001
                self.state, self.error = "error", str(e) or type(e).__name__
            finally:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            if done:
                done(self)
        threading.Thread(target=run, daemon=True).start()

    def check(self, done=None):
        """后台线程里检查; 完成后在那个线程里调用 done(self)."""
        with self._lock:
            if self.state in ("checking", "downloading"):
                return
            self.state = "checking"

        def run():
            try:
                with _open(API_LATEST, 20) as r:
                    rel = json.loads(r.read(2_000_000).decode("utf-8"))
                v = release_version(rel)
                if not v:
                    raise ValueError("no version number in the release")
                self.release, self.latest = rel, v
                self.state = "available" if v > self.current else "latest"
                self.error = ""
            except Exception as e:      # noqa: BLE001 —— 显示给用户
                self.state, self.error = "error", str(e) or type(e).__name__
            if done:
                done(self)
        threading.Thread(target=run, daemon=True).start()

    def install(self, done=None):
        """下载并覆盖程序文件; 完成后调用 done(self). 成功后 state = installed."""
        if self.state != "available" or not self.release:
            return
        if is_dev_checkout():
            self.state = "dev"
            if done:
                done(self)
            return
        self.state = "downloading"

        def run():
            path = None
            try:
                src, _zipball = pick_download(self.release)
                if not src or not src.get("url"):
                    raise ValueError("nothing to download")
                fd, path = tempfile.mkstemp(suffix=".zip", prefix="oit-")
                os.close(fd)
                download(src, path)
                install_zip(path, program_dir())
                self.state, self.error = "installed", ""
            except Exception as e:      # noqa: BLE001
                self.state, self.error = "error", str(e) or type(e).__name__
            finally:
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if done:
                done(self)
        threading.Thread(target=run, daemon=True).start()


def _selftest():
    assert parse_version("1") == (1, 0, 0) and parse_version("v1.2") == (1, 2, 0)
    assert parse_version("Python") is None and parse_version("1.2.3.4") == (1, 2, 3)
    assert release_version({"tag_name": "1", "name": "1.0.0"}) == (1, 0, 0)
    assert release_version({"tag_name": "v2.1", "name": "Spring update"}) == (2, 1, 0)
    assert (1, 1, 0) > (1, 0, 0) and parse_version(config.APP_VERSION)
    src, zb = pick_download({"assets": [{"name": "OIT-android.apk", "browser_download_url": "a"},
                                        {"name": "OIT-windows.zip", "browser_download_url": "b",
                                         "url": "https://api.github.com/repos/x/y/releases/assets/7",
                                         "size": 5, "digest": "sha256:ABCD"}],
                             "zipball_url": "z"})
    assert not zb and src == {"url": "https://api.github.com/repos/x/y/releases/assets/7",
                              "accept": ACCEPT_FILE, "size": 5, "sha256": "abcd"}, src
    src, zb = pick_download({"assets": [], "zipball_url": "z"})
    assert zb and src["url"] == "z" and src["sha256"] is None
    rel = {"assets": [{"name": "x.apk", "browser_download_url": "a1"},
                      {"name": "OIT-release.apk", "browser_download_url": "a2"}], "zipball_url": "z"}
    assert pick_apk(rel)["url"] == "a2" and package_source(rel, "android")["url"] == "a2"
    assert package_source(rel, "windows")["url"] == "z" and package_source(rel, "ios") is None
    assert pick_apk({"assets": []}) is None
    # 路线: 用户原来的代理 (不是 OIT 自己的过滤代理) 在前, 直连在后
    import tempfile as _tf
    from netproxy import sysproxy as _sp
    saved_bf, saved_rb = globals()["_backup_file"], _sp.RegistryBackend
    try:
        d = _tf.mkdtemp()
        bf = os.path.join(d, "b.json")
        globals()["_backup_file"] = lambda: bf
        with open(bf, "w", encoding="utf-8") as f:
            json.dump({"ours": "127.0.0.1:17890", "orig": {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7897"}}, f)
        assert upstream_proxy() == ("127.0.0.1", 7897)
        assert [r[0] for r in _routes()] == ["direct", "proxy"], "直连在前, 用户原来的代理在后"
        with open(bf, "w", encoding="utf-8") as f:
            json.dump({"ours": "127.0.0.1:17890", "orig": {"ProxyEnable": 0}}, f)
        assert upstream_proxy() is None and [r[0] for r in _routes()] == ["direct"]
        os.remove(bf)                  # 没有接管系统代理: 看现在的系统代理, OIT 自己的端口不算

        class _Reg:
            state = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:17891"}

            def read(self):
                return dict(self.state)
        _sp.RegistryBackend = _Reg
        import sys as _sys
        if _sys.platform == "win32":
            assert upstream_proxy() is None
            _Reg.state = {"ProxyEnable": 1, "ProxyServer": "10.0.0.2:8080"}
            assert upstream_proxy() == ("10.0.0.2", 8080)
    finally:
        globals()["_backup_file"] = saved_bf
        _sp.RegistryBackend = saved_rb

    # 下载失败: 不留下半截文件
    d = _tf.mkdtemp()
    try:
        download("https://evil.example.com/x", os.path.join(d, "x.apk"))
        raise AssertionError("non-GitHub download accepted")
    except ValueError:
        pass
    assert os.listdir(d) == []

    for bad in ("http://api.github.com/x", "https://evil.example.com/x"):
        try:
            _open(bad, 1)
            raise AssertionError(bad)
        except ValueError:
            pass

    # 覆盖安装: 源码包的顶层文件夹, 跳过安卓版, 不删多余文件
    src = tempfile.mkdtemp()
    zp = os.path.join(src, "a.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Obliphur-OIT-abc/main.py", "print('new')")
        z.writestr("Obliphur-OIT-abc/app.py", "x = 1")
        z.writestr("Obliphur-OIT-abc/pages/settings_page.py", "y = 2")
        z.writestr("Obliphur-OIT-abc/Android version/build.gradle.kts", "no")
    target = tempfile.mkdtemp()
    with open(os.path.join(target, "keep.txt"), "w") as f:
        f.write("mine")
    assert install_zip(zp, target) == 3
    assert install_zip(zp, target) == 0                # 内容没变: 一个都不覆盖
    with zipfile.ZipFile(zp, "a") as z:
        z.writestr("Obliphur-OIT-abc/themes/font.ttf", "v1")
    os.makedirs(os.path.join(target, "themes"))
    with open(os.path.join(target, "themes", "font.ttf"), "w") as f:
        f.write("old")
    real_copy = shutil.copy2

    def locked_copy(a, b, *args, **kw):
        if str(b).endswith(".ttf"):
            raise PermissionError(13, "in use")
        return real_copy(a, b, *args, **kw)
    shutil.copy2 = locked_copy
    try:
        install_zip(zp, target)                        # 被占用的资源文件: 跳过, 不算失败
    finally:
        shutil.copy2 = real_copy
    assert open(os.path.join(target, "themes", "font.ttf")).read() == "old"
    assert open(os.path.join(target, "main.py")).read() == "print('new')"
    assert os.path.exists(os.path.join(target, "pages", "settings_page.py"))
    assert not os.path.exists(os.path.join(target, "Android version"))
    assert os.path.exists(os.path.join(target, "keep.txt"))
    bad = os.path.join(src, "b.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("README.md", "no program")
    try:
        install_zip(bad, target)
        raise AssertionError("archive without program accepted")
    except ValueError:
        pass
    os.makedirs(os.path.join(target, ".git"))
    assert is_dev_checkout(target)
    return "updater selftest OK"


if __name__ == "__main__":
    print(_selftest())
