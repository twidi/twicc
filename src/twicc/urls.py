from django.urls import path, re_path

from . import views
from .artifacts.proxy import artifact_proxy
from .browser_probe import browser_frame_check
from .auth import views as auth_views
from .rpc import views as rpc_views
from .share import artifact_views as share_artifact_views
from .share import owner_views as share_owner_views
from .share import password_views as share_password_views
from .share import router as share_router
from .share import session_views as share_session_views
from .share import views_assets as share_views_assets
from .peer import inbound_views as peer_inbound_views
from .peer import owner_views as peer_owner_views

urlpatterns = [
    # Auth endpoints (always accessible, no auth required)
    path("api/auth/check/", auth_views.auth_check),
    path("api/auth/login/", auth_views.login),
    path("api/auth/logout/", auth_views.logout),
    # API endpoints
    path("api/bootstrap/", views.bootstrap),
    path("api/changelog/", views.changelog),
    path("api/home/", views.home_data),
    path("api/daily-activity/", views.daily_activity),  # Global daily activity
    path("api/sessions/", views.all_sessions),
    # Static route must come BEFORE the <str:session_id> catch-all, otherwise
    # `bulk-archive` is consumed as a session_id and matched by session_by_id.
    path("api/sessions/bulk-archive/", views.bulk_archive_sessions),
    path("api/sessions/<str:session_id>/", views.session_by_id),
    path("api/sessions/<str:session_id>/plan/", views.session_plan_content),
    path("api/search/", views.search_sessions),
    path("api/usage-history/", views.usage_history),
    path("api/external-notifications/test/", views.external_notifications_test),
    # Standalone filesystem endpoints (for directory picker, no project required)
    path("api/directory-tree/", views.standalone_directory_tree),
    path("api/file-search/", views.standalone_file_search),
    path("api/file-content/", views.standalone_file_content),
    path("api/file-raw/<str:root_b64>/<path:filepath>", views.standalone_file_raw),
    path("api/file-rename/", views.standalone_file_rename),
    path("api/file-delete/", views.standalone_file_delete),
    path("api/file-move/", views.standalone_file_move),
    path("api/file-create/", views.standalone_file_create),
    path("api/home-directory/", views.home_directory),
    path("api/artifact-bookmarks/", views.artifact_bookmark_list),
    path("api/artifact-bookmarks/<int:bookmark_id>/", views.artifact_bookmark_detail),
    path("api/artifact-bookmarks/<int:bookmark_id>/allowed-hosts/", views.artifact_bookmark_allowed_hosts),
    path("api/artifact-bookmarks/<int:bookmark_id>/denied-hosts/", views.artifact_bookmark_denied_hosts),
    path("api/artifact-bookmarks/<int:bookmark_id>/network-denials/", views.artifact_bookmark_network_denials),
    path("api/artifact-proxy/", artifact_proxy),
    path("api/browser-frame-check/", browser_frame_check),
    path("_twicc/artifact-broker-shim.js", views.artifact_broker_shim),
    path("_twicc/artifact-shell/<str:asset>", views.artifact_shell_asset),
    path("_twicc/browser-companion.js", views.browser_companion_script),
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/icon/", views.project_icon_manage),
    path("api/projects/<str:project_id>/trust/resolve/", views.project_trust_resolve),
    path("api/projects/<str:project_id>/trust/decide/", views.project_trust_decide),
    path("api/projects/<str:project_id>/branches/", views.project_branches),
    path("api/projects/<str:project_id>/resolve-git/", views.project_resolve_git),
    path("api/projects/<str:project_id>/refresh-directory/", views.project_refresh_directory),
    path("api/projects/<str:project_id>/worktrees/", views.project_worktrees),
    path("api/projects/<str:project_id>/worktrees/adopt/", views.project_worktree_adopt),
    path("api/projects/<str:project_id>/commands/", views.commands),
    path("api/projects/<str:project_id>/daily-activity/", views.daily_activity),  # Per-project daily activity
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    # Project-level file system endpoints (for draft sessions and project-level browsing)
    path("api/projects/<str:project_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/file-content/", views.file_content),
    path("api/projects/<str:project_id>/file-raw/<path:filepath>", views.file_raw),
    path("api/projects/<str:project_id>/file-rename/", views.file_rename),
    path("api/projects/<str:project_id>/file-delete/", views.file_delete),
    path("api/projects/<str:project_id>/file-move/", views.file_move),
    path("api/projects/<str:project_id>/file-create/", views.file_create),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/user-messages/", views.user_messages),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/subagents/", views.subagents_state),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/tool-states/", views.tool_states),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/topology/", views.session_topology),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/workflows/", views.session_workflows),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/workflows/<str:run_id>/synthesis/", views.workflow_synthesis),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/workflow-links/", views.workflow_links),
    # Subagent routes (same views, with parent_session_id for validation)
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    # Project-level git endpoints (for draft sessions)
    path("api/projects/<str:project_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/git-commit-detail/<str:commit_hash>/", views.git_commit_detail),
    path("api/projects/<str:project_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    path("api/projects/<str:project_id>/git-stage/", views.git_stage_file),
    path("api/projects/<str:project_id>/git-unstage/", views.git_unstage_file),
    path("api/projects/<str:project_id>/git-discard/", views.git_discard_file),
    path("api/projects/<str:project_id>/git-file-download/", views.git_file_download),
    path("api/projects/<str:project_id>/git-diff-download/", views.git_diff_download),
    # Git endpoints (session-level, no subagent support)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-detail/<str:commit_hash>/", views.git_commit_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-stage/", views.git_stage_file),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-unstage/", views.git_unstage_file),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-discard/", views.git_discard_file),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-file-download/", views.git_file_download),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-diff-download/", views.git_diff_download),
    # File system endpoints (scoped to project + session for security)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-content/", views.file_content),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-raw/<path:filepath>", views.file_raw),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-rename/", views.file_rename),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-delete/", views.file_delete),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-move/", views.file_move),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-create/", views.file_create),
    # Session-scoped artifact serving. Not nested under ``/api/`` because
    # this is a media endpoint rather than a JSON API; not nested under any
    # project/session SPA path because no project ownership is implied.
    # Must come before the SPA catch-all below — otherwise ``spa_index``
    # would happily serve ``index.html`` for these URLs. Authentication is
    # enforced by ``PasswordAuthMiddleware`` via its protected non-API
    # path list.
    # Standalone password page for direct artifact access (no SPA). Public; the
    # middleware redirects unauthenticated artifact navigations here.
    path("artifacts/auth", auth_views.artifact_auth),
    # Open a bookmarked artifact in its own tab, by bookmark id. The trailing
    # slash matters: it makes the page's relative assets resolve under the
    # bookmark's directory. ``<int:>`` keeps these from shadowing the UUID-keyed
    # ``session_artifact`` route below, so they must precede it.
    path("artifacts/<int:bookmark_id>", views.artifact_redirect_to_slash),
    path("artifacts/<int:bookmark_id>/", views.artifact_serve),
    path("artifacts/<int:bookmark_id>/<path:asset>", views.artifact_serve),
    path(
        "artifacts/<str:session_id>/<str:artifact_file_name>",
        views.session_artifact,
    ),
    # Project-icon serving. Like ``artifacts/`` it is a media endpoint (not
    # ``/api/``, not a project path), must precede the SPA catch-all, and is
    # auth-gated by ``PasswordAuthMiddleware``'s protected non-API list.
    path("project-icons/<str:bucket>/<str:file_name>", views.project_icon),
    # Owner-side share management (design §11). Under /api/ → password-gated.
    path("api/shares/", share_owner_views.shares_list),
    path("api/shares/<str:share_id>/", share_owner_views.share_detail),
    path("api/shares/<str:share_id>/revoke/", share_owner_views.share_revoke),
    path("api/shares/<str:share_id>/unrevoke/", share_owner_views.share_unrevoke),
    path("api/shares/<str:share_id>/propagate/", share_owner_views.share_propagate),
    path("api/shares/<str:share_id>/accesses/", share_owner_views.share_accesses),
    # Public share surface (design §6). Order: password page, kind-specific API,
    # then root + bottom catch (LAST so it can't shadow the API routes).
    path("share/<str:token>/auth", share_password_views.share_auth),
    path("share/<str:token>/api/meta/", share_session_views.api_meta),
    path("share/<str:token>/api/items/metadata/", share_session_views.api_items_metadata),
    path("share/<str:token>/api/items/", share_session_views.api_items),
    path("share/<str:token>/api/items/<int:line_num>/tool-results/<str:tool_id>/", share_session_views.api_tool_results),
    path("share/<str:token>/api/tool-states/", share_session_views.api_tool_states),
    path("share/<str:token>/api/backend-patch/<str:tool_id>/", share_session_views.api_backend_patch),
    path("share/<str:token>/api/subagents/", share_session_views.api_subagents),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/metadata/", share_session_views.api_subagent_items_metadata),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/", share_session_views.api_subagent_items),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/<int:line_num>/tool-results/<str:tool_id>/", share_session_views.api_subagent_tool_results),
    path("share/<str:token>/api/subagent/<str:subagent_id>/tool-states/", share_session_views.api_subagent_tool_states),
    path("share/<str:token>/api/subagent/<str:subagent_id>/backend-patch/<str:tool_id>/", share_session_views.api_subagent_backend_patch),
    path("share/<str:token>/media/<str:filename>", share_session_views.share_session_media),
    # Artifact-share meta + proxy live under /api/ too (shape-uniform with sessions).
    path("share/<str:token>/api/artifact-meta/", share_artifact_views.api_meta),
    path("share/<str:token>/api/proxy/", share_artifact_views.share_artifact_proxy),
    path("share/", share_router.share_recent),
    path("share/<str:token>/", share_router.share_root),
    path("share/<str:token>/<path:asset>", share_router.share_asset_or_doc),
    path("_twicc/share/<str:asset>", share_views_assets.share_asset),
    # Peer messaging (design 2026-07-24). Inbound instance-to-instance API:
    # Bearer-auth inside the views, exempt from the human auth gates (see
    # auth/middleware.PUBLIC_PATHS). Owner management under /api/ → cookie-gated.
    path("peer/handshake/request/", peer_inbound_views.handshake_request),
    path("peer/handshake/cancel/", peer_inbound_views.handshake_cancel),
    path("peer/handshake/verify/", peer_inbound_views.handshake_verify),
    path("peer/handshake/accept/", peer_inbound_views.handshake_accept),
    path("peer/messages/", peer_inbound_views.message_receive),
    path("peer/messages/<str:message_id>/status/", peer_inbound_views.message_status),
    path("api/peers/", peer_owner_views.peers_list),
    path("api/peers/<str:peer_id>/", peer_owner_views.peer_detail),
    path("api/peers/<str:peer_id>/verify/", peer_owner_views.peer_verify),
    path("api/peers/<str:peer_id>/accept/", peer_owner_views.peer_accept),
    path("api/peers/<str:peer_id>/refuse/", peer_owner_views.peer_refuse),
    path("api/peers/<str:peer_id>/reconnect/", peer_owner_views.peer_reconnect),
    path("api/peers/<str:peer_id>/reconnect/cancel/", peer_owner_views.peer_reconnect_cancel),
    path("api/peer-messages/", peer_owner_views.peer_messages_list),
    path("api/peer-messages/send/", peer_owner_views.peer_message_send),
    path("api/peer-messages/<int:pk>/", peer_owner_views.peer_message_detail),
    path("api/peer-messages/<int:pk>/attachments/", peer_owner_views.peer_message_attachments),
    path("api/peer-messages/<int:pk>/deliver/", peer_owner_views.peer_message_deliver),
    path("api/peer-messages/<int:pk>/link-session/", peer_owner_views.peer_message_link_session),
    path("api/peer-messages/<int:pk>/refuse/", peer_owner_views.peer_message_refuse),
    # RPC API: every CLI command auto-exposed as ``POST /rpc/<command>``.
    # Gated by Bearer API tokens via ``RpcTokenAuthMiddleware`` (open only when
    # neither a password nor any token is configured). Must precede the SPA
    # catch-all, which excludes ``rpc/`` so unknown RPC URLs 404 instead of
    # serving ``index.html``.
    path("rpc/", rpc_views.index),
    path("rpc/openapi.json", rpc_views.openapi),
    re_path(r"^rpc/(?P<command_path>[a-z0-9/-]+)/?$", rpc_views.dispatch),
    # Catch-all for Vue Router (must be last). ``artifacts/`` and ``rpc/`` are
    # excluded so those URLs surface as 404 instead of serving the SPA HTML.
    # Static files (/static/) are served by BlackNoise at the ASGI level,
    # before reaching Django's URL routing (see asgi.py).
    re_path(r"^(?!api/|rpc/|static/|ws/|artifacts/|project-icons/|share/|_twicc/|peer/).*$", views.spa_index),
]
