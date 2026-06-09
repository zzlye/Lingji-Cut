# 视频接口测试 - 验证手动下载封面会落到当前视频工作目录

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


# 嵌入式 Python 直接运行测试时手动加入项目根目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.api.videos import ThumbnailDownloadRequest, download_thumbnail  # noqa: E402
from backend.models import VideoSource  # noqa: E402


class VideoQuery:
    """测试用视频查询对象"""

    def __init__(self, video):
        self.video = video

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.video


class VideoDb:
    """测试用数据库会话"""

    def __init__(self, video):
        self.video = video

    def query(self, *_args, **_kwargs):
        return VideoQuery(self.video)


class VideosApiTests(unittest.TestCase):
    """视频接口测试"""

    def test_download_thumbnail_uses_video_workspace_root_dir(self):
        """手动下载封面默认保存到该视频独立工作目录根目录"""
        video = VideoSource(
            id=7,
            video_id="abc123",
            platform="youtube",
            url="https://example.com/watch?v=abc123",
            title="Test Video",
            thumbnail_url="https://example.com/cover.webp",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            expected_path = os.path.join(temp_dir, "Test_Video_cover.webp")
            with patch("backend.api.videos.ensure_video_workspace", return_value={"workspace_dir": temp_dir, "downloads_dir": os.path.join(temp_dir, "downloads")}):
                with patch("backend.api.videos.Downloader.download_thumbnail", return_value=expected_path) as download_mock:
                    response = download_thumbnail(ThumbnailDownloadRequest(video_id=7), db=VideoDb(video))

        self.assertEqual(response.output_path, expected_path)
        self.assertEqual(response.message, "封面下载完成")
        self.assertEqual(download_mock.call_args.kwargs["output_dir"], temp_dir)
        self.assertEqual(download_mock.call_args.kwargs["thumbnail_url"], video.thumbnail_url)
        self.assertEqual(download_mock.call_args.kwargs["file_name"], "Test_Video_cover")

    def test_download_thumbnail_uses_custom_output_dir(self):
        """手动下载封面指定目录时保存到用户选择的位置"""
        video = VideoSource(
            id=8,
            video_id="custom123",
            platform="youtube",
            url="https://example.com/watch?v=custom123",
            title="Custom Cover",
            thumbnail_url="https://example.com/cover.jpg",
        )

        with tempfile.TemporaryDirectory() as workspace_dir:
            with tempfile.TemporaryDirectory() as custom_dir:
                expected_path = os.path.join(custom_dir, "Custom_Cover_cover.jpg")
                with patch("backend.api.videos.ensure_video_workspace", return_value={"workspace_dir": workspace_dir, "downloads_dir": os.path.join(workspace_dir, "downloads")}):
                    with patch("backend.api.videos.Downloader.download_thumbnail", return_value=expected_path) as download_mock:
                        response = download_thumbnail(ThumbnailDownloadRequest(video_id=8, output_dir=custom_dir), db=VideoDb(video))

        self.assertEqual(response.output_path, expected_path)
        self.assertEqual(download_mock.call_args.kwargs["output_dir"], custom_dir)


if __name__ == "__main__":
    unittest.main()
