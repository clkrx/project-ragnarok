from pathlib import Path


# A single filesystem tool with an "operation" selector keeps the tool surface
# small while still covering the common actions an agent needs on Windows.
FILE_TOOL = {
    "name": "file",
    "description": (
        "Read, write, and manage files and folders on the user's Windows 11 "
        "system. Paths may be absolute (e.g. C:\\Users\\me\\notes.txt) or "
        "relative to the current working directory. Supported operations:\n"
        "- read:   return the text contents of a file\n"
        "- write:  create or overwrite a file with the given content\n"
        "- append: add content to the end of a file (creating it if needed)\n"
        "- list:   list the entries in a directory\n"
        "- mkdir:  create a directory (and any missing parents)\n"
        "- delete: delete a file or an (empty or non-empty) directory\n"
        "- move:   move/rename a file or directory to 'destination'\n"
        "- exists: report whether a path exists and what type it is"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "read",
                    "write",
                    "append",
                    "list",
                    "mkdir",
                    "delete",
                    "move",
                    "exists",
                ],
                "description": "The filesystem action to perform.",
            },
            "path": {
                "type": "string",
                "description": "The target file or directory path.",
            },
            "content": {
                "type": "string",
                "description": "Text content, required for 'write' and 'append'.",
            },
            "destination": {
                "type": "string",
                "description": "Destination path, required for 'move'.",
            },
        },
        "required": ["operation", "path"],
    },
}


def run_file(
    operation: str,
    path: str,
    content: str = "",
    destination: str = "",
) -> dict:
    """Perform a filesystem operation and return a structured result."""
    try:
        target = Path(path).expanduser()

        if operation == "read":
            text = target.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "path": str(target), "content": text}

        if operation == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(target), "bytes_written": len(content)}

        if operation == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "path": str(target), "bytes_appended": len(content)}

        if operation == "list":
            if not target.is_dir():
                return {"ok": False, "error": f"Not a directory: {target}"}
            entries = []
            for child in sorted(target.iterdir()):
                entries.append(
                    {
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    }
                )
            return {"ok": True, "path": str(target), "entries": entries}

        if operation == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}

        if operation == "delete":
            if not target.exists():
                return {"ok": False, "error": f"Path does not exist: {target}"}
            if target.is_dir():
                import shutil

                shutil.rmtree(target)
            else:
                target.unlink()
            return {"ok": True, "path": str(target), "deleted": True}

        if operation == "move":
            if not destination:
                return {"ok": False, "error": "'destination' is required for move."}
            dest = Path(destination).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            target.rename(dest)
            return {"ok": True, "from": str(target), "to": str(dest)}

        if operation == "exists":
            if not target.exists():
                return {"ok": True, "exists": False, "path": str(target)}
            return {
                "ok": True,
                "exists": True,
                "path": str(target),
                "type": "dir" if target.is_dir() else "file",
            }

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
