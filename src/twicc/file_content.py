"""File content business logic: read file contents for the code viewer.

Extracted following the same pattern as file_tree.py — views stay thin HTTP wrappers.
"""

import os

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def get_file_content(file_path):
    """Read a file and return its content as a dict.

    Returns:
        {
            "content": str,       # file content (absent if binary)
            "size": int,          # file size in bytes
            "binary": bool,       # True if file is binary
            "error": str | None,  # error message if any
        }

    Handles:
    - Files too large (>5MB): returns error
    - Binary files (UTF-8 decode fails): returns binary=True, no content
    - Normal text files: returns content + size
    """
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return {"error": "Cannot read file", "size": 0, "binary": False}

    if size > MAX_FILE_SIZE:
        return {
            "error": f"File too large ({size / 1024 / 1024:.1f} MB, max {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)",
            "size": size,
            "binary": False,
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return {"content": None, "size": size, "binary": True}
    except OSError:
        return {"error": "Cannot read file", "size": size, "binary": False}

    return {"content": content, "size": size, "binary": False}
