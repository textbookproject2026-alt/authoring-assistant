"""
Secrets, kept in the macOS Keychain rather than in a file.

Two secrets pass through here: the author's GitHub sign-in token, and the
optional DeepSeek key. Both used to live in plain files under Application
Support; the Keychain is where a Mac is supposed to keep this kind of thing, and
having one policy for both is easier to explain than having two.

Everything goes through /usr/bin/security, which ships with macOS. The secret is
written on standard input, never as a command-line argument, so it never appears
in the process list.

Every function here is allowed to fail. A Mac with a locked keychain, a denied
permission prompt, or a missing item all return None or False rather than
raising. Callers treat "no secret" and "could not read the secret" the same way:
they ask the author to sign in again.
"""

import subprocess

SECURITY = "/usr/bin/security"
SERVICE = "Authoring Assistant"

# Two items under one service, told apart by account name.
ACCOUNT_GITHUB = "github-token"
ACCOUNT_DEEPSEEK = "deepseek-key"

TIMEOUT = 20


def _run(args, stdin_text=None):
    """Run the security tool. Returns (ok, stdout). Never raises."""
    try:
        proc = subprocess.run(
            [SECURITY] + args,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return proc.returncode == 0, proc.stdout


def available():
    """Is the Keychain usable on this Mac at all?"""
    ok, _ = _run(["list-keychains"])
    return ok


def save(account, secret):
    """Store (or replace) a secret. Returns True if it is definitely stored."""
    secret = (secret or "").strip()
    if not secret:
        return False
    # The tool asks for the password twice when reading it from standard input,
    # so it is sent twice. -U replaces an existing item instead of erroring.
    ok, _ = _run(
        ["add-generic-password", "-a", account, "-s", SERVICE, "-U", "-w"],
        stdin_text=secret + "\n" + secret + "\n",
    )
    if not ok:
        return False
    # Trust nothing: read it back rather than believing the exit code.
    return load(account) == secret


def load(account):
    """Return the stored secret, or None if there isn't one we can read."""
    ok, out = _run(["find-generic-password", "-a", account, "-s", SERVICE, "-w"])
    if not ok:
        return None
    secret = out.strip()
    return secret or None


def delete(account):
    """Forget a secret. True if something was removed."""
    ok, _ = _run(["delete-generic-password", "-a", account, "-s", SERVICE])
    return ok


def hint(account):
    """The last four characters, so the author can tell which secret is saved
    without ever seeing it again."""
    secret = load(account)
    if not secret:
        return None
    return secret[-4:] if len(secret) > 4 else "****"
