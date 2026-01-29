from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, re_path

from . import views

urlpatterns = [
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    # Catch-all for Vue Router (must be last)
    re_path(r"^(?!api/|static/|ws/).*$", views.spa_index),
]

# Serve static files in DEBUG mode
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
