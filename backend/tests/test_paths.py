# backend/tests/test_paths.py
# 文件位置回归测试 - 项目根目录只自动创建 videos，避免多余公共文件夹

import os
import tempfile
import unittest

from backend.core.paths import ensure_project_dirs


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


if __name__ == "__main__":
    unittest.main()
