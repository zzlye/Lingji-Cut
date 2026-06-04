# backend/core/paths.py
# 项目文件夹配置 - 统一管理下载、输出和导出目录

import json
import os
from typing import Any


# 可写数据根目录：打包环境用 Electron 通过 YTV_DATA_ROOT 传入的用户数据目录，
# 开发环境回退到项目根目录（打包后程序目录通常只读，不能在那里建库和写配置）。
DATA_ROOT = os.environ.get("YTV_DATA_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 首次启动时的默认项目目录
APP_ROOT = DATA_ROOT

# 设置文件固定保存在数据根目录的 data 子目录，避免用户切换项目目录后找不到配置
CONFIG_DIR = os.path.join(DATA_ROOT, "data")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

# 项目目录下自动创建的业务子目录
PROJECT_SUBDIRS = {
    "data_dir": "data",
    "downloads_dir": "downloads",
    "output_dir": "output",
    "exports_dir": "exports",
}


def normalize_project_root(project_root: str | None) -> str:
    """规范化项目目录路径"""
    if not project_root or not project_root.strip():
        return APP_ROOT
    return os.path.abspath(os.path.expanduser(project_root.strip()))


def _read_settings() -> dict[str, Any]:
    """读取本地设置文件，文件不存在或损坏时返回空配置"""
    if not os.path.exists(CONFIG_PATH):
        return {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_project_root() -> str:
    """获取当前项目目录"""
    settings = _read_settings()
    return normalize_project_root(settings.get("project_root"))


def save_project_root(project_root: str) -> str:
    """保存项目目录，并创建必要的业务子目录"""
    root = normalize_project_root(project_root)
    ensure_project_dirs(root)

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump({"project_root": root}, file, ensure_ascii=False, indent=2)

    return root


def reset_project_root() -> str:
    """恢复默认项目目录"""
    return save_project_root(APP_ROOT)


def ensure_project_dirs(project_root: str | None = None) -> dict[str, str]:
    """确保项目目录及其子目录存在，并返回完整路径"""
    root = normalize_project_root(project_root) if project_root else load_project_root()
    os.makedirs(root, exist_ok=True)

    paths = {"project_root": root, "default_project_root": APP_ROOT}
    for key, dirname in PROJECT_SUBDIRS.items():
        path = os.path.join(root, dirname)
        os.makedirs(path, exist_ok=True)
        paths[key] = path
    return paths


def get_project_paths(create: bool = True) -> dict[str, dict[str, Any]]:
    """返回前端需要展示的项目路径信息"""
    raw_paths = ensure_project_dirs() if create else _build_project_paths(load_project_root())
    return {
        key: {
            "path": value,
            "exists": os.path.exists(value),
        }
        for key, value in raw_paths.items()
    }


def _build_project_paths(project_root: str) -> dict[str, str]:
    """只计算路径，不创建目录"""
    root = normalize_project_root(project_root)
    paths = {"project_root": root, "default_project_root": APP_ROOT}
    for key, dirname in PROJECT_SUBDIRS.items():
        paths[key] = os.path.join(root, dirname)
    return paths
