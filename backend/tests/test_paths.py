# backend/tests/test_paths.py
# 文件位置回归测试 - 项目根目录只自动创建 videos，避免多余公共文件夹

import os
import tempfile
import unittest

from backend.core.paths import ensure_project_dirs, ensure_video_workspace, find_video_workspace


class ProjectPathsTest(unittest.TestCase):
    def test_project_root_only_creates_videos_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_project_dirs(temp_dir)

            self.assertTrue(os.path.isdir(paths["videos_dir"]))
            self.assertEqual(paths["downloads_dir"], paths["videos_dir"])
            self.assertEqual(paths["output_dir"], paths["videos_dir"])
            self.assertEqual(paths["exports_dir"], paths["videos_dir"])

            for dirname in ("data", "downloads", "output", "exports"):
                self.assertFalse(os.path.exists(os.path.join(temp_dir, dirname)))

    def test_video_workspace_uses_title_before_video_id(self):
        """新建视频项目目录时标题在前，视频 ID 放后面"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = ensure_video_workspace("ZHfS8Q8-OtQ", "How To Master The Mace In 2 Minutes", temp_dir)

            self.assertEqual(paths["workspace_name"], "How_To_Master_The_Mace_In_2_Minutes__ZHfS8Q8-OtQ")
            self.assertTrue(os.path.isdir(paths["workspace_dir"]))

    def test_video_workspace_reuses_legacy_id_first_directory(self):
        """旧版 ID 在前的目录继续复用，避免历史素材断链"""
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_dir = os.path.join(temp_dir, "videos", "ZHfS8Q8-OtQ__How_To_Master_The_Mace_In_2_Minutes")
            os.makedirs(legacy_dir)

            paths = ensure_video_workspace("ZHfS8Q8-OtQ", "How To Master The Mace In 2 Minutes", temp_dir)

            self.assertEqual(paths["workspace_dir"], legacy_dir)

    def test_find_video_workspace_does_not_create_missing_directory(self):
        """只读查找缺失项目时不创建空文件夹，避免素材库刷新生成测试目录"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = find_video_workspace("local-asr", "测试视频", temp_dir)

            self.assertIsNone(paths)
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "videos")))

    def test_find_video_workspace_reads_existing_title_first_directory(self):
        """只读查找能识别标题在前的新目录"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = os.path.join(temp_dir, "videos", "测试视频__local-asr")
            os.makedirs(workspace_dir)

            paths = find_video_workspace("local-asr", "测试视频", temp_dir)

            self.assertIsNotNone(paths)
            self.assertEqual(paths["workspace_dir"], workspace_dir)


if __name__ == "__main__":
    unittest.main()
