"""A minimal python_repl for platforms where strands_tools.python_repl can't be imported.

strands_tools.python_repl needs pty, fcntl and os.fork, which don't exist on Windows.
This version runs each snippet in a fresh Python subprocess, so variables do not persist
between calls (the agent prompts already assume that).
"""

import subprocess
import sys

from strands import tool

TIMEOUT_SECONDS = 60


@tool
def python_repl(code: str) -> str:
    """Execute Python code and return what it prints.

    Each call runs in a fresh interpreter, so variables from earlier calls are not available.
    Use print() to show results.

    Args:
        code: The Python code to run.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Error: code did not finish within {TIMEOUT_SECONDS} seconds."
    output = result.stdout
    if result.returncode != 0:
        output += result.stderr
    return output or "(no output)"
