from sqlalchemy.orm import DeclarativeBase

# 导入模型以便 Alembic 自动识别元数据
from app import models  # noqa: F401


class Base(DeclarativeBase):
    pass
