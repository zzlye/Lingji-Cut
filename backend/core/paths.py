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

# 项目目录下只自动创建 videos；每个视频自己的 downloads/output/exports 会建在 videos/<视频项目>/ 下。
PROJECT_SUBDIRS = {
    "videos_dir": "videos",
}

WORKSPACE_STAGE_DIRS = {
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

    paths = _build_project_paths(root)
    for key, dirname in PROJECT_SUBDIRS.items():
        path = os.path.join(root, dirname)
        os.makedirs(path, exist_ok=True)
        paths[key] = path
    return paths


def ensure_video_workspace(video_id: str | int | None, title: str | None, project_root: str | None = None) -> dict[str, str]:
    """确保单个视频的独立工作目录存在，并返回该视频专属的下载/输出/导出路径"""
    base_paths = ensure_project_dirs(project_root)
    compatibility_workspace = _compat_workspace_from_base_paths(base_paths)
    if compatibility_workspace:
        return compatibility_workspace

    project_root_path = base_paths.get("project_root") or load_project_root()
    videos_root = base_paths.get("videos_dir") or os.path.join(project_root_path, "videos")
    os.makedirs(videos_root, exist_ok=True)

    workspace_name = _resolve_workspace_name(videos_root, video_id, title)
    workspace_dir = os.path.join(videos_root, workspace_name)
    os.makedirs(workspace_dir, exist_ok=True)

    paths = {
        "project_root": project_root_path,
        "default_project_root": base_paths.get("default_project_root") or APP_ROOT,
        "videos_dir": videos_root,
        "workspace_dir": workspace_dir,
        "workspace_name": workspace_name,
    }
    for key, dirname in WORKSPACE_STAGE_DIRS.items():
        path = os.path.join(workspace_dir, dirname)
        os.makedirs(path, exist_ok=True)
        paths[key] = path
    return paths


def detect_video_workspace(media_path: str) -> dict[str, str] | None:
    """根据已有媒体文件路径回推所属视频工作目录"""
    normalized = os.path.abspath(os.path.expanduser(str(media_path or "").strip()))
    if not normalized:
        return None

    stage_dir = os.path.dirname(normalized)
    workspace_dir = os.path.dirname(stage_dir)
    stage_name = os.path.basename(stage_dir).lower()
    if stage_name not in set(WORKSPACE_STAGE_DIRS.values()):
        return None
    if not os.path.isdir(workspace_dir):
        return None

    paths = {
        "project_root": os.path.dirname(os.path.dirname(workspace_dir)),
        "default_project_root": APP_ROOT,
        "videos_dir": os.path.dirname(workspace_dir),
        "workspace_dir": workspace_dir,
        "workspace_name": os.path.basename(workspace_dir),
    }
    for key, dirname in WORKSPACE_STAGE_DIRS.items():
        paths[key] = os.path.join(workspace_dir, dirname)
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
    videos_dir = paths["videos_dir"]
    # 旧字段保留给少量兜底逻辑使用，但不再映射到项目根目录的公共子文件夹。
    paths["downloads_dir"] = videos_dir
    paths["output_dir"] = videos_dir
    paths["exports_dir"] = videos_dir
    paths["data_dir"] = CONFIG_DIR
    return paths


def _compat_workspace_from_base_paths(base_paths: dict[str, str]) -> dict[str, str] | None:
    """兼容单元测试或旧逻辑传入的简化目录结构，避免强行再套一层 videos 目录"""
    if "project_root" in base_paths:
        return None
    fallback_dir = base_paths.get("output_dir") or base_paths.get("downloads_dir") or base_paths.get("exports_dir")
    if not fallback_dir:
        return None
    workspace_dir = os.path.abspath(os.path.expanduser(fallback_dir))
    return {
        "project_root": os.path.dirname(workspace_dir),
        "default_project_root": APP_ROOT,
        "videos_dir": os.path.dirname(workspace_dir),
        "workspace_dir": workspace_dir,
        "workspace_name": os.path.basename(workspace_dir),
        "downloads_dir": base_paths.get("downloads_dir") or workspace_dir,
        "output_dir": base_paths.get("output_dir") or workspace_dir,
        "exports_dir": base_paths.get("exports_dir") or workspace_dir,
    }


def _resolve_workspace_name(videos_root: str, video_id: str | int | None, title: str | None) -> str:
    """根据视频 ID 和标题生成稳定目录名；同一个视频改标题时尽量复用旧目录"""
    safe_id = _safe_path_fragment(str(video_id or "").strip(), fallback="video")
    prefix = f"{safe_id}__"
    for dirname in os.listdir(videos_root):
        if dirname.startswith(prefix):
            return dirname
    safe_title = _safe_path_fragment(title or "", fallback="untitled")[:64]
    return f"{safe_id}__{safe_title}"


def _safe_path_fragment(value: str, fallback: str = "item") -> str:
    """把任意标题转换成安全可读的目录名片段"""
    text = str(value or "").strip()
    text = "".join(char if char.isalnum() or char in ("-", "_", ".", " ") else "_" for char in text)
    text = "_".join(text.split())
    text = text.strip("._")
    return text or fallback
