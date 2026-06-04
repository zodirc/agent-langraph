"""Project-level verify backends (web_html_js, make_cpp_demo)."""

from app.services.project_verify.backends import (
    ProjectVerifyResult,
    resolve_project_backend_id,
    verify_project,
)

__all__ = [
    "ProjectVerifyResult",
    "resolve_project_backend_id",
    "verify_project",
]
