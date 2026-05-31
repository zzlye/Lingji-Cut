# backend/models/database.py
# 数据库连接配置 - SQLAlchemy + SQLite

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# 数据库文件路径 - 存放在项目根目录的 data 文件夹
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "app.db")

# 创建数据库引擎
# connect_args={"check_same_thread": False} 允许多线程访问 SQLite
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


def get_db():
    """获取数据库会话（用于 FastAPI 依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库 - 创建所有表"""
    Base.metadata.create_all(bind=engine)
    _migrate_subtitle_presets()
    _migrate_voice_profiles()


def _migrate_subtitle_presets():
    """补齐旧版本字幕预设表缺失的列"""
    inspector = inspect(engine)
    if "subtitle_presets" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("subtitle_presets")}
    required_columns = {
        "language": "VARCHAR(50) DEFAULT 'auto'",
        "secondary_color": "VARCHAR(20) DEFAULT '#FDE68A'",
        "shadow_enabled": "BOOLEAN DEFAULT 1",
        "shadow_x": "INTEGER DEFAULT 2",
        "shadow_y": "INTEGER DEFAULT 2",
        "background_alpha": "INTEGER DEFAULT 0",
    }

    with engine.begin() as connection:
        for column_name, column_sql in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE subtitle_presets ADD COLUMN {column_name} {column_sql}"))


def _migrate_voice_profiles():
    """补齐旧版本配音配置表缺失的列"""
    inspector = inspect(engine)
    if "voice_provider_profiles" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("voice_provider_profiles")}
    required_columns = {
        "extra_params": "TEXT",
    }

    with engine.begin() as connection:
        for column_name, column_sql in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE voice_provider_profiles ADD COLUMN {column_name} {column_sql}"))
