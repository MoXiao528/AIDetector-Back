# app/db/base.py
from app.db.base_class import Base  # noqa

import app.models.user  # noqa: F401
import app.models.api_key  # noqa: F401
import app.models.scan_history  # noqa: F401
import app.models.team  # noqa: F401
import app.models.user_profile  # noqa: F401
import app.models.wallet  # noqa: F401
