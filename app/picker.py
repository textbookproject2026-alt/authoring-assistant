"""
The real macOS file and folder choosers.

The author never types a path. These call the same open panels that any Mac
application uses, through osascript, so nothing extra needs installing.
"""

import subprocess

CHOOSE_FILE = '''
tell application "System Events"
    activate
    set theFile to choose file with prompt "%s" of type {%s} %s
end tell
POSIX path of theFile
'''

MARKDOWN_TYPES = '"md", "markdown", "txt"'
# Word's own file type, and the identifier Finder knows it by. Both are given
# because a .docx that has arrived by email sometimes carries only the second.
WORD_TYPES = '"docx", "org.openxmlformats.wordprocessingml.document"'

CHOOSE_FILE_ANY = '''
tell application "System Events"
    activate
    set theFile to choose file with prompt "%s" %s
end tell
POSIX path of theFile
'''

CHOOSE_FOLDER = '''
tell application "System Events"
    activate
    set theFolder to choose folder with prompt "%s" %s
end tell
POSIX path of theFolder
'''


def _default_clause(start_in):
    if not start_in:
        return ""
    escaped = start_in.replace('"', '\\"')
    return f'default location POSIX file "{escaped}"'


def _run(script):
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "The chooser could not be opened."
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        if "cancel" in err:
            return None, None          # the author simply changed their mind
        return None, (proc.stderr or "The chooser could not be opened.").strip()
    path = proc.stdout.strip()
    return (path or None), None


def choose_file(prompt="Choose the chapter you want to work on", start_in=None,
                types=None):
    script = CHOOSE_FILE % (prompt.replace('"', "'"), types or MARKDOWN_TYPES,
                            _default_clause(start_in))
    path, err = _run(script)
    if path is None and err:
        # Some Macs reject the file-type filter; fall back to showing everything.
        script = CHOOSE_FILE_ANY % (prompt.replace('"', "'"),
                                    _default_clause(start_in))
        path, err = _run(script)
    return path, err


def choose_word_document(prompt="Choose the Word document", start_in=None):
    return choose_file(prompt, start_in, types=WORD_TYPES)


def choose_folder(prompt="Choose your vault folder", start_in=None):
    script = CHOOSE_FOLDER % (prompt.replace('"', "'"), _default_clause(start_in))
    return _run(script)


def obsidian_running():
    try:
        proc = subprocess.run(["pgrep", "-x", "Obsidian"],
                              capture_output=True, text=True, timeout=5)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
