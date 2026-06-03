import logging
import re
import subprocess


log = logging.getLogger(__name__)


# This is the tool definition Claude sees.
# It tells Claude what the tool does and what inputs it takes.
POWERSHELL_TOOL = {
    "name": "powershell",
    "description": (
        "Execute a PowerShell command on the user's Windows 11 system. "
        "Returns stdout, stderr, and the exit code. "
        "Use this for system queries, launching programs, inspecting system "
        "state, networking, package management, etc. "
        "This is Windows PowerShell (5.1) syntax, NOT bash: use Get-ChildItem "
        "instead of ls, Get-Content instead of cat, $env:NAME for environment "
        "variables, and the backtick (`) as the line-continuation character. "
        "For routine file reading/writing/listing, prefer the 'file' tool. "
        "Commands run with the user's permissions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The PowerShell command to execute.",
            },
        },
        "required": ["command"],
    },
}


# --- Safety layer ----------------------------------------------------------
#
# This is a GUARDRAIL against ACCIDENTAL destructive commands (e.g. a bad tool
# call from the model), NOT a real security sandbox. The patterns below stop the
# obvious catastrophic mistakes before they run; a determined attacker could
# trivially bypass them. Keep it that way on purpose -- it's meant to be simple
# and easy to read, audit, and extend.
#
# Each entry is (regex_pattern, human_readable_reason). All patterns are matched
# case-insensitively, and use \s* / \s+ so extra spaces don't sneak past them.
# Some patterns use (?=...) lookaheads, which just mean "the command also
# contains this somewhere", so flag order (e.g. -Recurse before or after the
# path) doesn't matter.
DENYLIST = [
    # Recursive delete (Remove-Item / its aliases, or cmd's rd /s) aimed at a
    # whole drive root, the user profile, or a Windows system directory.
    (
        r"\b(remove-item|ri|rm|rmdir|rd|del|erase)\b"
        r"(?=.*(-r(ec(urse)?)?\b|\s/s\b))"
        r"(?=.*([a-z]:\\(\s|$|[\"';|])|\$home\b|\$env:userprofile|%userprofile%"
        r"|\$env:systemroot|%systemroot%|\\windows\b))",
        "recursive delete of a drive root, the user profile, or a system directory",
    ),
    # Writing straight to a raw physical disk or volume device, which bypasses
    # the filesystem and corrupts the disk (the Windows equivalent of dd to /dev).
    (
        r"\\\\\.\\(physicaldrive\d+|[a-z]:)",
        "raw write to a physical disk / volume device (\\\\.\\PhysicalDrive...)",
    ),
    # Formatting a drive, wiping/partitioning a disk, or diskpart -- destroys
    # everything on the target (the Windows equivalent of mkfs).
    (
        r"(\bformat(\.com)?\s+[a-z]:|\bformat-volume\b|\bclear-disk\b"
        r"|\binitialize-disk\b|\bnew-partition\b|\bdiskpart\b)",
        "disk format / partition / wipe (format X:, Format-Volume, Clear-Disk, diskpart)",
    ),
    # Privilege escalation. Huginn should never need admin rights for V1.
    (
        r"(^|[\s;&|])(sudo|gsudo|runas)\b|start-process\b.*-verb\s+runas",
        "privilege escalation (sudo / runas / Start-Process -Verb RunAs)",
    ),
    # Fork bomb / runaway process spawner: the classic batch %0|%0, or an
    # infinite loop that keeps launching new processes.
    (
        r"%0\s*\|\s*%0|while\s*\(\s*\$?true\s*\)\s*\{[^}]*\b(start-process|start)\b",
        "fork bomb / runaway process spawner",
    ),
    # Recursively taking ownership of or rewriting permissions on a drive root or
    # system directory (the Windows equivalent of chmod -R / / chown -R /).
    (
        r"\b(icacls|takeown|set-acl)\b"
        r"(?=.*([a-z]:\\(\s|$)|\\windows\b|\$env:systemroot))"
        r"(?=.*(/t\b|/r\b|-recurse))",
        "recursive permission/ownership change on a drive root or system directory",
    ),
    # Downloading a remote script and piping it straight into a shell to execute
    # (the Windows equivalent of curl ... | sh).
    (
        r"\b(iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod)\b.*\|\s*"
        r"(iex|invoke-expression|powershell|pwsh|cmd|&)\b"
        r"|\b(iex|invoke-expression)\b\s*\(?\s*"
        r"(iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod|new-object\s+net\.webclient)",
        "piping a remotely downloaded script straight into a shell (iwr ... | iex)",
    ),
]


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check a command against the denylist.

    Returns (True, "") if no catastrophic pattern matched, or
    (False, reason) describing the first pattern that did.
    """
    for pattern, reason in DENYLIST:
        if re.search(pattern, command, re.IGNORECASE):
            return False, reason
    return True, ""


def run_powershell(command: str, timeout: int = 30) -> dict:
    """Run a PowerShell command and return structured output."""
    # Guardrail check happens BEFORE anything is executed. If it's blocked we
    # return the normal result shape (stdout/stderr/exit_code) with a clear
    # message and a non-zero status, so the agent sees the block, can explain it,
    # and the tool loop keeps running instead of crashing or hanging.
    safe, reason = is_command_safe(command)
    if not safe:
        return {
            "stdout": "",
            "stderr": f"BLOCKED by safety layer: {reason}",
            "exit_code": 126,  # conventional "command found but cannot execute"
        }

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        log.info(
            "powershell exit_code=%s cmd=%r",
            result.returncode,
            command[:500],
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        log.warning("powershell timeout cmd=%r", command[:500])
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
            "exit_code": -1,
        }
    except Exception as e:
        log.error("powershell error cmd=%r err=%s", command[:500], e)
        return {
            "stdout": "",
            "stderr": f"Error running command: {e}",
            "exit_code": -1,
        }
