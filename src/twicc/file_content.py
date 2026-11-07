"""File content business logic: read and write file contents for the code viewer/editor.

Extracted following the same pattern as file_tree.py — views stay thin HTTP wrappers.
"""

import base64
import os
import shutil

from twicc.core.ui_messages import ui_message_text
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

# Mapping of image file extensions to MIME types.
# When a binary file matches one of these, its content is returned as a data URI.
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _get_image_src(file_path, size):
    """Return a data URI for the image file, or None if not an image or too large."""
    ext = os.path.splitext(file_path)[1].lower()
    mime_type = IMAGE_EXTENSIONS.get(ext)
    if not mime_type:
        return None
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def get_file_meta(file_path):
    """Return lightweight metadata for a file or directory (no content read).

    Returns:
        {"writable": bool, "type": "file"|"directory", "size": int, "mtime": float}
        (``mtime`` in epoch seconds) or {"error": str} on failure.
    """
    try:
        stat = os.stat(file_path)
    except OSError:
        return {"error": ui_message_text("Path not found", 'backend.file_content.path_missing')}
    path_type = "directory" if os.path.isdir(file_path) else "file"
    parent_dir = os.path.dirname(file_path)
    writable = os.access(parent_dir, os.W_OK) and os.access(file_path, os.W_OK)
    return {"writable": writable, "type": path_type, "size": stat.st_size, "mtime": stat.st_mtime}


def get_file_content(file_path):
    """Read a file and return its content as a dict.

    Returns:
        {
            "content": str,       # file content (absent if binary)
            "size": int,          # file size in bytes
            "binary": bool,       # True if file is binary
            "image_src": str,     # data URI for image files (only when binary + image)
            "error": str | None,  # error message if any
        }

    Handles:
    - Files too large (>5MB): returns error
    - Binary files (UTF-8 decode fails): returns binary=True, no content
      - If the file is a known image type, also returns image_src as a data URI
    - Normal text files: returns content + size
    """
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return {"error": ui_message_text("Cannot read file", 'backend.file_content.read_failed'), "size": 0, "binary": False}

    if size > MAX_FILE_SIZE:
        return {
            "error": ui_message_text(f"File too large ({size / 1024 / 1024:.1f} MB, max {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)", 'backend.file_content.file_too_large', {"size": f"{size / 1024 / 1024:.1f}", "maximum": f"{MAX_FILE_SIZE / 1024 / 1024:.0f}"}),
            "size": size,
            "binary": False,
            "too_large": True,
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        result = {"content": None, "size": size, "binary": True}
        image_src = _get_image_src(file_path, size)
        if image_src:
            result["image_src"] = image_src
        return result
    except OSError:
        return {"error": ui_message_text("Cannot read file", 'backend.file_content.read_failed'), "size": size, "binary": False}

    writable = os.access(file_path, os.W_OK)
    return {"content": content, "size": size, "binary": False, "writable": writable}


def write_file_content(file_path, content):
    """Write content to an existing file.

    Returns:
        {"ok": True} on success
        {"error": str} on failure

    Handles:
    - Content too large (>5MB): returns error
    - File does not exist: returns error
    - Permission / OS errors: returns error
    """
    if not os.path.isfile(file_path):
        return {"error": ui_message_text("File not found", 'backend.file_content.file_missing')}

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_FILE_SIZE:
        return {
            "error": ui_message_text(f"Content too large ({len(content_bytes) / 1024 / 1024:.1f} MB, max {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)", 'backend.file_content.content_too_large', {"size": f"{len(content_bytes) / 1024 / 1024:.1f}", "maximum": f"{MAX_FILE_SIZE / 1024 / 1024:.0f}"})
        }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return {"error": ui_message_text(f"Cannot write file: {e}", 'backend.file_content.write_file_failed', {"detail": str(e)})}

    return {"ok": True}


def create_path(parent_dir, name, kind):
    """Create a new file or directory inside parent_dir.

    Args:
        parent_dir: absolute path of the parent directory
        name: name of the new file or directory
        kind: "file" or "directory"

    Returns:
        {"ok": True, "new_path": str} on success
        {"error": str} on failure
    """
    if "/" in name or "\\" in name or not name.strip():
        return {"error": ui_message_text("Invalid name", 'backend.file_content.invalid_name')}

    if not os.path.isdir(parent_dir):
        return {"error": ui_message_text("Parent directory not found", 'backend.file_content.parent_missing')}

    if not os.access(parent_dir, os.W_OK):
        return {"error": ui_message_text("Permission denied", 'backend.file_content.permission_denied')}

    new_path = os.path.join(parent_dir, name)
    if os.path.exists(new_path):
        return {"error": ui_message_text("A file or directory with this name already exists", 'backend.file_content.name_exists')}

    try:
        if kind == "directory":
            os.makedirs(new_path)
        else:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write("")
    except OSError as e:
        return {"error": ui_message_text(f"Cannot create: {e}", 'backend.file_content.create_failed', {"detail": str(e)})}

    return {"ok": True, "new_path": new_path}


def move_path(source_path, destination_dir):
    """Move a file or directory into a different directory.

    Returns:
        {"ok": True, "new_path": str} on success
        {"error": str} on failure
    """
    if not os.path.exists(source_path):
        return {"error": ui_message_text("Source not found", 'backend.file_content.source_missing')}

    if not os.path.isdir(destination_dir):
        return {"error": ui_message_text("Destination directory not found", 'backend.file_content.destination_missing')}

    source_parent = os.path.dirname(source_path)
    if os.path.realpath(source_parent) == os.path.realpath(destination_dir):
        return {"error": ui_message_text("Source is already in this directory", 'backend.file_content.same_directory')}

    if not os.access(source_parent, os.W_OK):
        return {"error": ui_message_text("Permission denied on source directory", 'backend.file_content.source_permission_denied')}

    if not os.access(destination_dir, os.W_OK):
        return {"error": ui_message_text("Permission denied on destination directory", 'backend.file_content.destination_permission_denied')}

    name = os.path.basename(source_path)
    new_path = os.path.join(destination_dir, name)
    if os.path.exists(new_path):
        return {"error": ui_message_text(f"'{name}' already exists in the destination directory", 'backend.file_content.destination_exists', {"name": name})}

    # Prevent moving a directory into itself or a subdirectory of itself
    if os.path.isdir(source_path):
        real_src = os.path.realpath(source_path)
        real_dst = os.path.realpath(destination_dir)
        if real_dst == real_src or real_dst.startswith(real_src + os.sep):
            return {"error": ui_message_text("Cannot move a directory into itself", 'backend.file_content.move_into_self')}

    try:
        shutil.move(source_path, new_path)
    except OSError as e:
        return {"error": ui_message_text(f"Cannot move: {e}", 'backend.file_content.move_failed', {"detail": str(e)})}

    return {"ok": True, "new_path": new_path}


def rename_path(old_path, new_name):
    """Rename a file or directory (same parent directory, new name).

    Returns:
        {"ok": True, "new_path": str} on success
        {"error": str} on failure
    """
    if not os.path.exists(old_path):
        return {"error": ui_message_text("Path not found", 'backend.file_content.path_missing')}

    if "/" in new_name or "\\" in new_name or not new_name.strip():
        return {"error": ui_message_text("Invalid name", 'backend.file_content.invalid_name')}

    parent_dir = os.path.dirname(old_path)
    if not os.access(parent_dir, os.W_OK):
        return {"error": ui_message_text("Permission denied", 'backend.file_content.permission_denied')}

    new_path = os.path.join(parent_dir, new_name)
    if os.path.exists(new_path):
        return {"error": ui_message_text("A file or directory with this name already exists", 'backend.file_content.name_exists')}

    try:
        os.rename(old_path, new_path)
    except OSError as e:
        return {"error": ui_message_text(f"Cannot rename: {e}", 'backend.file_content.rename_failed', {"detail": str(e)})}

    return {"ok": True, "new_path": new_path}


def delete_path(file_path):
    """Delete a file or directory (recursive for directories).

    Returns:
        {"ok": True} on success
        {"error": str} on failure
    """
    if not os.path.exists(file_path):
        return {"error": ui_message_text("Path not found", 'backend.file_content.path_missing')}

    parent_dir = os.path.dirname(file_path)
    if not os.access(parent_dir, os.W_OK):
        return {"error": ui_message_text("Permission denied", 'backend.file_content.permission_denied')}

    try:
        if os.path.isdir(file_path):
            shutil.rmtree(file_path)
        else:
            os.remove(file_path)
    except OSError as e:
        return {"error": ui_message_text(f"Cannot delete: {e}", 'backend.file_content.delete_failed', {"detail": str(e)})}

    return {"ok": True}
