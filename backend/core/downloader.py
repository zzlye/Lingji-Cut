# backend/core/downloader.py
# yt-dlp 下载封装 - 调用 yt-dlp CLI 下载 YouTube 视频

import os
import re
import json
import subprocess
import time
import mimetypes
import urllib.request
from urllib.parse import urlparse
from typing import Optional, Callable
from ..utils import get_logger
from .paths import ensure_project_dirs
from .process_control import TaskControlRequested, normalize_control_keys, raise_if_control_requested, register_process, subprocess_creation_flags, terminate_process, unregister_process
from .tooling import get_ffmpeg_command, get_yt_dlp_command

# 日志记录器
logger = get_logger("downloader")

class Downloader:
    """yt-dlp 下载封装类"""

    def __init__(self):
        """初始化下载器，检查 yt-dlp 是否可用"""
        self.yt_dlp_cmd = get_yt_dlp_command()
        logger.info(f"yt-dlp 路径: {self.yt_dlp_cmd}")

    def parse_video(self, url: str) -> dict:
        """
        解析 YouTube 视频信息
        返回：标题、作者、时长、清晰度、字幕轨、缩略图
        """
        logger.info(f"解析视频: {url}")

        # 构建 yt-dlp 命令
        cmd = [
            self.yt_dlp_cmd,
            "--dump-json",           # 输出 JSON 格式
            "--no-download",         # 不下载视频
            "--no-warnings",         # 不显示警告
            url
        ]

        try:
            # 执行 yt-dlp 命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()
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
        cmd = [
            self.yt_dlp_cmd,
            "-o", output_template,
            "--merge-output-format", output_format,
            "--no-warnings",
        ]

        # 指定格式
        if format_id:
            cmd.extend(["-f", format_id])
        else:
            # 默认只下载 1080p 以内的源，避免后续画面处理先解码 2K/4K 再降采样。
            cmd.extend(["-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080][ext=mp4]/best[height<=1080]/best"])

        # 添加 ffmpeg 路径，确保指定格式和默认格式都能使用本地合并工具。
        ffmpeg_command = get_ffmpeg_command()
        if os.path.isabs(ffmpeg_command):
            cmd.extend(["--ffmpeg-location", os.path.dirname(ffmpeg_command)])

        cmd.append(url)

        logger.info(f"开始下载: {url}")

        control_keys = normalize_control_keys(control_keys)
        process = None
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
                raise RuntimeError(f"下载失败，退出码: {process.returncode}")

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

        cmd = [
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
            cmd.append("--write-auto-sub")

        cmd.append(url)

        logger.info(f"下载字幕: {url} (语言: {language})")

        control_keys = normalize_control_keys(control_keys)
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
