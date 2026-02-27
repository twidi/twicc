from django.urls import path, re_path

from . import views
from .auth import views as auth_views

urlpatterns = [
    # Auth endpoints (always accessible, no auth required)
    path("api/auth/check/", auth_views.auth_check),
    path("api/auth/login/", auth_views.login),
    path("api/auth/logout/", auth_views.logout),
    # API endpoints
    path("api/home/", views.home_data),
    path("api/daily-activity/", views.daily_activity),  # Global daily activity
    path("api/sessions/", views.all_sessions),
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/daily-activity/", views.daily_activity),  # Per-project daily activity
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    # Project-level file system endpoints (for draft sessions and project-level browsing)
    path("api/projects/<str:project_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/file-content/", views.file_content),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/<int:line_num>/tool-agent-id/<str:tool_id>/", views.tool_agent_id),
    # Subagent routes (same views, with parent_session_id for validation)
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    # Project-level git endpoints (for draft sessions)
    path("api/projects/<str:project_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    # Git endpoints (session-level, no subagent support)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    # File system endpoints (scoped to project + session for security)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-content/", views.file_content),
    # Catch-all for Vue Router (must be last)
    re_path(r"^(?!api/|static/|ws/).*$", views.spa_index),
]

# Serve static files directly (no reverse proxy in front).
# Uses our own serve_static view instead of Django's django.views.static.serve
# to avoid the StreamingHttpResponse warning under ASGI.
urlpatterns += [
    re_path(r"^static/(?P<path>.*)$", views.serve_static),
]
