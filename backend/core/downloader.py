# backend/core/downloader.py
# yt-dlp 下载封装 - 调用 yt-dlp CLI 下载 YouTube 视频

import os
import re
import json
import platform
import subprocess
import time
import mimetypes
import urllib.request
from urllib.parse import urlparse
from typing import Optional, Callable
from ..utils import get_logger
from .paths import ensure_project_dirs, load_ytdlp_cookie_settings
from .process_control import TaskControlRequested, normalize_control_keys, raise_if_control_requested, register_process, subprocess_creation_flags, terminate_process, unregister_process
from .tooling import get_ffmpeg_command, get_yt_dlp_command

# 日志记录器
logger = get_logger("downloader")

class Downloader:
    """yt-dlp 下载封装类"""

    def __init__(self):
        """初始化下载器，检查 yt-dlp 是否可用"""
        self.yt_dlp_cmd = get_yt_dlp_command()
        self._active_cookie_args: list[str] = []
        logger.info(f"yt-dlp 路径: {self.yt_dlp_cmd}")

    def parse_video(self, url: str) -> dict:
        """
        解析 YouTube 视频信息
        返回：标题、作者、时长、清晰度、字幕轨、缩略图
        """
        logger.info(f"解析视频: {url}")

        # 构建 yt-dlp 命令
        base_cmd = [
            self.yt_dlp_cmd,
            "--dump-json",           # 输出 JSON 格式
            "--no-download",         # 不下载视频
            "--no-warnings",         # 不显示警告
        ]

        try:
            result, error_msg = self._run_yt_dlp_json_with_cookie_retry(base_cmd, url, timeout=60)

            if result.returncode != 0:
                logger.error(f"解析失败: {error_msg}")
                raise RuntimeError(f"视频解析失败: {error_msg}")

            # 解析 JSON 输出
            info = json.loads(result.stdout)

            # 提取所需信息
            video_info = {
                "video_id": info.get("id", ""),
                "platform": "youtube",
                "url": url,
                "title": info.get("title", ""),
                "author": info.get("uploader", info.get("channel", "")),
                "duration": info.get("duration", 0),
                "thumbnail_url": info.get("thumbnail", ""),
                # 提取可用清晰度列表
                "formats": self._extract_formats(info.get("formats", [])),
                # 提取可用字幕轨
                "subtitles": self._extract_subtitles(info),
            }

            logger.info(f"解析成功: {video_info['title']}")
            return video_info

        except subprocess.TimeoutExpired:
            raise RuntimeError("视频解析超时（60秒）")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"解析 yt-dlp 输出失败: {e}")

    def _run_yt_dlp_json_with_cookie_retry(self, base_cmd: list[str], url: str, timeout: int) -> tuple[subprocess.CompletedProcess, str]:
        """执行 yt-dlp JSON 命令，遇到 YouTube 机器人验证时自动尝试浏览器 cookies"""
        result = self._run_yt_dlp_json_once(base_cmd, url, self._initial_cookie_args(), timeout)
        error_msg = self._yt_dlp_error_text(result)
        if result.returncode == 0 or not self._needs_cookie_retry(error_msg):
            return result, error_msg

        retry_errors: list[str] = []
        for cookie_args in self._browser_cookie_retry_args():
            result = self._run_yt_dlp_json_once(base_cmd, url, cookie_args, timeout)
            error_msg = self._yt_dlp_error_text(result)
            if result.returncode == 0:
                self._active_cookie_args = cookie_args
                logger.info(f"yt-dlp 已使用浏览器 cookies 重试成功: {cookie_args[-1] if cookie_args else ''}")
                return result, error_msg
            retry_errors.append(error_msg)

        return result, self._cookie_retry_failure_message("视频解析", error_msg, retry_errors)

    def _run_yt_dlp_json_once(self, base_cmd: list[str], url: str, cookie_args: list[str], timeout: int) -> subprocess.CompletedProcess:
        """执行单次 yt-dlp JSON 命令"""
        cmd = self._build_yt_dlp_command(base_cmd, url, cookie_args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout
        )

    def _yt_dlp_error_text(self, result: subprocess.CompletedProcess) -> str:
        """合并 yt-dlp 的标准错误和标准输出，保留真实失败原因"""
        stderr = str(result.stderr or "").strip()
        stdout = str(result.stdout or "").strip()
        return stderr or stdout

    def _build_yt_dlp_command(self, base_cmd: list[str], url: str, cookie_args: Optional[list[str]] = None) -> list[str]:
        """在 URL 前插入 cookies 参数，避免 yt-dlp 把 cookies 参数误当成地址"""
        cmd = list(base_cmd)
        if cookie_args:
            cmd.extend(cookie_args)
        cmd.append(url)
        return cmd

    def _initial_cookie_args(self) -> list[str]:
        """读取当前可用 cookies 参数，优先复用已验证成功的浏览器 cookies"""
        if self._active_cookie_args:
            return list(self._active_cookie_args)

        cookie_file = str(os.environ.get("YTV_YTDLP_COOKIES_FILE") or "").strip()
        cookie_settings = load_ytdlp_cookie_settings()
        if not cookie_file:
            cookie_file = str(cookie_settings.get("cookies_file") or "").strip()
        if cookie_file and os.path.isfile(os.path.expanduser(cookie_file)):
            return ["--cookies", os.path.expanduser(cookie_file)]
        if cookie_file:
            logger.warning(f"已配置的 cookies.txt 不存在，跳过文件读取: {cookie_file}")

        browsers = self._configured_cookie_browsers(cookie_settings)
        if browsers:
            return ["--cookies-from-browser", browsers[0]]
        return []

    def _configured_cookie_browsers(self, cookie_settings: Optional[dict] = None) -> list[str]:
        """读取用户指定的浏览器 cookies 来源，支持逗号分隔多个候选"""
        settings = cookie_settings if cookie_settings is not None else load_ytdlp_cookie_settings()
        raw_value = str(os.environ.get("YTV_YTDLP_COOKIES_BROWSER") or settings.get("cookies_browser") or "").strip()
        if not raw_value:
            return []
        browsers: list[str] = []
        for item in raw_value.split(","):
            browser = item.strip()
            if browser and browser not in browsers:
                browsers.append(browser)
        return browsers

    def _default_cookie_browsers(self) -> list[str]:
        """按系统选择常见浏览器 cookies 来源"""
        system_name = platform.system().lower()
        if system_name == "windows":
            return ["edge", "chrome", "firefox"]
        if system_name == "darwin":
            return ["chrome", "safari", "firefox", "edge"]
        return ["chrome", "chromium", "firefox", "edge"]

    def _browser_cookie_retry_args(self) -> list[list[str]]:
        """生成浏览器 cookies 重试参数，跳过已经尝试过的初始参数"""
        candidates = self._configured_cookie_browsers() or self._default_cookie_browsers()
        initial = self._initial_cookie_args()
        results: list[list[str]] = []
        for browser in candidates:
            cookie_args = ["--cookies-from-browser", browser]
            if cookie_args == initial or cookie_args in results:
                continue
            results.append(cookie_args)
        return results

    def _needs_cookie_retry(self, error_text: str) -> bool:
        """判断 yt-dlp 是否遇到 YouTube 登录或机器人验证拦截"""
        text = str(error_text or "").lower()
        markers = (
            "sign in to confirm",
            "confirm you’re not a bot",
            "confirm you're not a bot",
            "cookies-from-browser",
            "pass cookies",
        )
        return any(marker in text for marker in markers)

    def _cookie_retry_failure_message(self, action: str, last_error: str, retry_errors: list[str]) -> str:
        """生成 cookies 重试失败提示，不输出任何 cookies 内容"""
        browser_names = ", ".join(self._configured_cookie_browsers() or self._default_cookie_browsers())
        details = self._summarize_cookie_retry_errors(last_error, retry_errors)
        hint = (
            f"{action}遇到 YouTube 登录验证，已尝试读取本机浏览器 cookies（{browser_names}）但仍失败。"
            "请在设置里的「YouTube 登录 Cookies」选择导出的 cookies.txt，"
            "或完全关闭 Chrome/Edge 后重试浏览器读取。"
        )
        return f"{details}\n{hint}".strip()

    def _summarize_cookie_retry_errors(self, last_error: str, retry_errors: list[str]) -> str:
        """压缩 cookies 重试错误，保留 Chrome 数据库占用等关键原因"""
        all_errors = [str(item or "").strip() for item in [last_error, *retry_errors] if str(item or "").strip()]
        summary: list[str] = []
        if any("could not copy chrome cookie database" in error.lower() for error in all_errors):
            summary.append("Chrome/Edge cookies 数据库复制失败：通常是浏览器正在运行或配置文件被占用。")
        if any("sign in to confirm" in error.lower() or "confirm you" in error.lower() for error in all_errors):
            summary.append("读取到的 cookies 仍没有有效 YouTube 登录态，或 YouTube 仍要求真人验证。")
        for error in all_errors:
            first_line = next((line.strip() for line in error.splitlines() if line.strip()), "")
            if first_line and first_line not in summary:
                summary.append(first_line)
            if len(summary) >= 4:
                break
        return "\n".join(summary)

    def _run_with_cookie_retry(self, action: str, runner: Callable[[list[str]], str]) -> str:
        """执行需要 yt-dlp 的任务，遇到验证拦截时自动换浏览器 cookies 重试"""
        first_cookie_args = self._initial_cookie_args()
        try:
            return runner(first_cookie_args)
        except RuntimeError as exc:
            first_error = str(exc)
            if not self._needs_cookie_retry(first_error):
                raise

        retry_errors: list[str] = []
        for cookie_args in self._browser_cookie_retry_args():
            try:
                result = runner(cookie_args)
                self._active_cookie_args = cookie_args
                logger.info(f"yt-dlp 已使用浏览器 cookies 重试成功: {cookie_args[-1] if cookie_args else ''}")
                return result
            except RuntimeError as retry_exc:
                retry_errors.append(str(retry_exc))

        raise RuntimeError(self._cookie_retry_failure_message(action, first_error, retry_errors))

    def _extract_formats(self, formats: list) -> list:
        """提取可用清晰度列表"""
        result = []
        seen = set()

        for f in formats:
            format_id = f.get("format_id", "")
            resolution = f.get("resolution", "audio only")
            ext = f.get("ext", "")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")

            # 跳过重复格式
            key = f"{resolution}_{ext}"
            if key in seen:
                continue
            seen.add(key)

            # 只保留有视频流的格式
            if vcodec != "none":
                result.append({
                    "format_id": format_id,
                    "resolution": resolution,
                    "ext": ext,
                    "fps": f.get("fps", 0),
                    "filesize": f.get("filesize", 0),
                    "vcodec": vcodec,
                    "acodec": acodec,
                })

        # 按分辨率排序
        result.sort(key=lambda x: x.get("resolution", ""), reverse=True)
        return result[:20]  # 只返回前 20 个格式

    def _extract_subtitles(self, info: dict) -> list:
        """提取可用字幕轨"""
        result = []

        # 原始字幕
        subtitles = info.get("subtitles", {})
        for lang, subs in subtitles.items():
            for sub in subs:
                result.append({
                    "language": lang,
                    "name": sub.get("name", lang),
                    "ext": sub.get("ext", "vtt"),
                    "type": "original",
                })

        # 自动字幕
        auto_captions = info.get("automatic_captions", {})
        for lang, subs in auto_captions.items():
            for sub in subs:
                result.append({
                    "language": lang,
                    "name": sub.get("name", f"{lang} (自动)"),
                    "ext": sub.get("ext", "vtt"),
                    "type": "auto",
                })

        return result

    def download_video(
        self,
        url: str,
        output_dir: Optional[str] = None,
        format_id: Optional[str] = None,
        output_format: str = "mp4",
        progress_callback: Optional[Callable[[float, str], None]] = None,
        control_keys: Optional[list[str]] = None,
    ) -> str:
        """
        下载视频
        返回：下载后的文件路径
        """
        if output_dir is None:
            output_dir = ensure_project_dirs()["downloads_dir"]

        os.makedirs(output_dir, exist_ok=True)

        # 输出文件名模板
        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

        # 构建 yt-dlp 命令
        base_cmd = [
            self.yt_dlp_cmd,
            "-o", output_template,
            "--merge-output-format", output_format,
            "--no-warnings",
        ]

        # 指定格式
        if format_id:
            base_cmd.extend(["-f", format_id])
        else:
            # 默认只下载 1080p 以内的源，避免后续画面处理先解码 2K/4K 再降采样。
            base_cmd.extend(["-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080][ext=mp4]/best[height<=1080]/best"])

        # 添加 ffmpeg 路径，确保指定格式和默认格式都能使用本地合并工具。
        ffmpeg_command = get_ffmpeg_command()
        if os.path.isabs(ffmpeg_command):
            base_cmd.extend(["--ffmpeg-location", os.path.dirname(ffmpeg_command)])

        logger.info(f"开始下载: {url}")

        control_keys = normalize_control_keys(control_keys)
        return self._run_with_cookie_retry(
            "视频下载",
            lambda cookie_args: self._download_video_once(
                base_cmd=base_cmd,
                url=url,
                output_dir=output_dir,
                progress_callback=progress_callback,
                control_keys=control_keys,
                cookie_args=cookie_args,
            ),
        )

    def _download_video_once(
        self,
        base_cmd: list[str],
        url: str,
        output_dir: str,
        progress_callback: Optional[Callable[[float, str], None]],
        control_keys: list[str],
        cookie_args: list[str],
    ) -> str:
        """执行单次视频下载，调用方负责决定是否重试"""
        cmd = self._build_yt_dlp_command(base_cmd, url, cookie_args)
        process = None
        output_lines: list[str] = []
        try:
            raise_if_control_requested(control_keys)
            # 执行下载命令
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess_creation_flags(),
            )
            register_process(control_keys, process, cmd)

            # 读取输出并解析进度
            output_file = None
            for line in process.stdout:
                raise_if_control_requested(control_keys)
                line = line.strip()
                if not line:
                    continue
                output_lines.append(line)

                # 解析下载进度
                progress_match = re.search(r'\[download\]\s+([\d.]+)%', line)
                if progress_match and progress_callback:
                    progress = float(progress_match.group(1))
                    progress_callback(progress, line)

                # 解析输出文件路径
                dest_match = re.search(r'\[Merger\] Merging formats into "(.+)"', line)
                if dest_match:
                    output_file = dest_match.group(1)
                elif re.search(r'\[download\] Destination: (.+)', line):
                    output_file = re.search(r'\[download\] Destination: (.+)', line).group(1)
                elif re.search(r'\[download\] (.+) has already been downloaded', line):
                    output_file = re.search(r'\[download\] (.+) has already been downloaded', line).group(1)

            process.wait()
            raise_if_control_requested(control_keys)
            if process.returncode != 0:
                tail_output = "\n".join(output_lines[-8:]).strip()
                detail = f": {tail_output}" if tail_output else ""
                raise RuntimeError(f"下载失败，退出码: {process.returncode}{detail}")

            if output_file and os.path.exists(output_file):
                logger.info(f"下载完成: {output_file}")
                return output_file
            else:
                # 尝试在输出目录中查找最新文件
                files = sorted(
                    [os.path.join(output_dir, f) for f in os.listdir(output_dir)],
                    key=os.path.getmtime,
                    reverse=True
                )
                if files:
                    logger.info(f"下载完成: {files[0]}")
                    return files[0]
                raise RuntimeError("下载完成但未找到输出文件")

        except subprocess.TimeoutExpired:
            if process:
                terminate_process(process)
            raise RuntimeError("下载超时")
        except TaskControlRequested:
            raise
        finally:
            if process:
                unregister_process(process)

    def download_subtitle(
        self,
        url: str,
        language: str = "en",
        output_dir: Optional[str] = None,
        sub_type: str = "original",
        control_keys: Optional[list[str]] = None,
    ) -> str:
        """下载字幕文件"""
        if output_dir is None:
            output_dir = ensure_project_dirs()["downloads_dir"]

        os.makedirs(output_dir, exist_ok=True)

        # 输出模板
        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
        start_time = time.time()

        base_cmd = [
            self.yt_dlp_cmd,
            "--skip-download",      # 不下载视频
            "--write-sub",          # 写入字幕
            "--sub-lang", language,
            "--sub-format", "vtt/srt/best",
            "-o", output_template,
            "--no-warnings",
            "--force-overwrites",   # 强制刷新同语言字幕，避免复用旧文件误判成功
            "--socket-timeout", "20", # 单个字幕请求网络卡住时尽快进入下一个候选
        ]

        # 自动字幕
        if sub_type == "auto":
            base_cmd.append("--write-auto-sub")

        logger.info(f"下载字幕: {url} (语言: {language})")

        control_keys = normalize_control_keys(control_keys)
        return self._run_with_cookie_retry(
            "字幕下载",
            lambda cookie_args: self._download_subtitle_once(
                base_cmd=base_cmd,
                url=url,
                output_dir=output_dir,
                language=language,
                start_time=start_time,
                control_keys=control_keys,
                cookie_args=cookie_args,
            ),
        )

    def _download_subtitle_once(
        self,
        base_cmd: list[str],
        url: str,
        output_dir: str,
        language: str,
        start_time: float,
        control_keys: list[str],
        cookie_args: list[str],
    ) -> str:
        """执行单次字幕下载，调用方负责决定是否重试"""
        cmd = self._build_yt_dlp_command(base_cmd, url, cookie_args)
        process = None
        try:
            raise_if_control_requested(control_keys)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess_creation_flags(),
            )
            register_process(control_keys, process, cmd)
            stdout, stderr = process.communicate(timeout=120)
            raise_if_control_requested(control_keys)

            if process.returncode != 0:
                raise RuntimeError(f"字幕下载失败: {stderr or stdout}")

            # 只查找本次语言对应的字幕，避免下载失败时误拿输出目录里的旧字幕文件。
            expected_suffixes = tuple(f".{language}.{ext}" for ext in ("vtt", "srt", "ass"))
            files = sorted(
                [os.path.join(output_dir, f) for f in os.listdir(output_dir)
                 if f.endswith(expected_suffixes) and os.path.getmtime(os.path.join(output_dir, f)) >= start_time - 1],
                key=os.path.getmtime,
                reverse=True
            )

            if files:
                logger.info(f"字幕下载完成: {files[0]}")
                return files[0]

            raise RuntimeError("字幕下载完成但未找到文件")

        except subprocess.TimeoutExpired:
            if process:
                terminate_process(process)
            raise RuntimeError("字幕下载超时（120秒）")
        except TaskControlRequested:
            raise
        finally:
            if process:
                unregister_process(process)

    def download_thumbnail(
        self,
        thumbnail_url: str,
        output_dir: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> str:
        """下载视频封面到本地，供用户手动保存缩略图素材"""
        if not thumbnail_url or not str(thumbnail_url).strip():
            raise ValueError("封面地址不能为空")

        if output_dir is None:
            output_dir = ensure_project_dirs()["downloads_dir"]
        os.makedirs(output_dir, exist_ok=True)

        request = urllib.request.Request(
            str(thumbnail_url).strip(),
            headers={"User-Agent": "Mozilla/5.0"},
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                content_type = ""
                if hasattr(response.headers, "get_content_type"):
                    content_type = response.headers.get_content_type() or ""
                else:
                    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        except Exception as exc:
            raise RuntimeError(f"封面下载失败: {exc}") from exc

        if not payload:
            raise RuntimeError("封面下载失败：返回内容为空")

        extension = self._guess_thumbnail_extension(str(thumbnail_url), content_type)
        output_name = self._safe_thumbnail_file_name(file_name or "thumbnail", extension)
        output_path = os.path.join(output_dir, output_name)
        with open(output_path, "wb") as file:
            file.write(payload)

        logger.info(f"封面下载完成: {output_path}")
        return output_path

    def _guess_thumbnail_extension(self, thumbnail_url: str, content_type: str) -> str:
        """优先按响应类型判断封面后缀，避免 WebP/JPEG 保存错扩展名"""
        guessed = mimetypes.guess_extension(str(content_type or "").strip(), strict=False)
        if guessed:
            if guessed == ".jpe":
                return ".jpg"
            return guessed

        path = urlparse(str(thumbnail_url or "")).path
        suffix = os.path.splitext(path)[1].lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            return suffix
        return ".jpg"

    def _safe_thumbnail_file_name(self, file_name: str, extension: str) -> str:
        """把标题整理成安全文件名，并补齐封面后缀"""
        safe_name = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in str(file_name or "").strip())
        safe_name = safe_name.strip("._") or "thumbnail"
        base_name, current_ext = os.path.splitext(safe_name)
        if current_ext.lower() != extension.lower():
            safe_name = f"{base_name or 'thumbnail'}{extension}"
        return safe_name
