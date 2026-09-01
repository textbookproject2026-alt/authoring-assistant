"""
Optional DeepSeek pass for glossary extraction.

Everything here is optional and everything here can fail. No missing key, no
network, a rate limit, a timeout, a malformed answer - none of it is allowed to
stop the tool. Every failure returns (None, reason) and the caller quietly uses
the deterministic result instead, showing the reason in the interface.
"""

import json
import os
import re
import urllib.error
import urllib.request

from . import config, keychain

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 45

# The current home for the key, then the place older versions used.
KEY_PATHS = [config.KEY_FILE, config.LEGACY_KEY_FILE]

SYSTEM = (
    "You help an academic author build a glossary for a scholarly book. "
    "You are given the text of one chapter. Identify technical terms that a "
    "reader would need defined: terms of art, named concepts, and specialist "
    "vocabulary the chapter itself introduces or relies on. "
    "Ignore ordinary words, author surnames, citations, and section titles. "
    "For each term, write a one-sentence definition grounded strictly in what "
    "this chapter says. Do not invent meanings the chapter does not support. "
    'Reply with JSON only, in the form '
    '{"terms": [{"term": "...", "definition": "..."}]}. '
    "Return at most 25 terms."
)


def key_path():
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    return KEY_PATHS[0]


def key_hint():
    """The last four characters, so the author can tell which key is saved
    without the key itself ever being shown again."""
    key = load_key()
    if not key:
        return None
    return key[-4:] if len(key) > 4 else "****"


def clear_key():
    """Forget the saved key. The environment variable, if set, is not ours."""
    removed = keychain.delete(keychain.ACCOUNT_DEEPSEEK)
    for p in KEY_PATHS:
        try:
            os.remove(p)
            removed = True
        except OSError:
            pass
    return removed


def test_key(key=None):
    """Check a key actually works, so the author finds out now rather than
    halfway through a chapter. Returns (ok, plain-English message)."""
    key = (key or load_key() or "").strip()
    if not key:
        return False, "There is no key saved yet."

    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply with the word: ready"}],
        "max_tokens": 4,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read(200)
        return True, "The key works. DeepSeek answered normally."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, ("DeepSeek did not accept that key. Check you copied "
                           "all of it, with no spaces at either end.")
        if e.code == 402:
            return False, ("The key is valid, but the DeepSeek account has no "
                           "credit left.")
        if e.code == 429:
            return False, ("The key looks fine, but DeepSeek is busy right now. "
                           "Try again in a minute.")
        return False, f"DeepSeek returned an error ({e.code})."
    except urllib.error.URLError:
        return False, ("DeepSeek could not be reached. This is usually the "
                       "internet connection.")
    except (TimeoutError, OSError):
        return False, "DeepSeek took too long to answer."
    except Exception:
        return False, "Something went wrong contacting DeepSeek."


def load_key():
    """The key comes from the environment, the Keychain, or an older key file.

    Earlier versions kept the key in a file. If one is still there it is moved
    into the Keychain the first time it is read, so there is eventually one place
    for both of the app's secrets rather than two.
    """
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env

    stored = keychain.load(keychain.ACCOUNT_DEEPSEEK)
    if stored:
        return stored

    for p in KEY_PATHS:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
        except OSError:
            continue
        if not key:
            continue
        # Move it, and only delete the file once the Keychain has it.
        if keychain.save(keychain.ACCOUNT_DEEPSEEK, key):
            try:
                os.remove(p)
            except OSError:
                pass
        return key
    return None


def have_key():
    return bool(load_key())


def _extract_json(content):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def suggest_terms(chapter_text, max_chars=90000):
    """Ask DeepSeek for glossary terms.

    Returns (terms, reason). terms is None whenever anything at all went wrong,
    and reason is a plain-English sentence suitable for showing to the author.
    """
    key = load_key()
    if not key:
        return None, ("No DeepSeek key is set up, so the plain checks were used "
                      "on their own.")

    text = chapter_text
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
        "stream": False,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return None, ("The DeepSeek service was busy (rate limited), so the "
                          "plain checks were used on their own.")
        if e.code in (401, 403):
            return None, ("DeepSeek did not accept the saved key, so the plain "
                          "checks were used on their own. You can set a new key "
                          "by running Setup again.")
        if e.code == 402:
            return None, ("The DeepSeek account has no credit left, so the plain "
                          "checks were used on their own.")
        return None, (f"DeepSeek returned an error ({e.code}), so the plain checks "
                      "were used on their own.")
    except urllib.error.URLError:
        return None, ("DeepSeek could not be reached, so the plain checks were "
                      "used on their own. This is usually the internet connection.")
    except (TimeoutError, OSError):
        return None, ("DeepSeek took too long to answer, so the plain checks were "
                      "used on their own.")
    except Exception:
        return None, ("Something went wrong contacting DeepSeek, so the plain "
                      "checks were used on their own.")

    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None, ("DeepSeek sent back something unexpected, so the plain "
                      "checks were used on their own.")

    parsed = _extract_json(content)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("terms"), list):
        return None, ("DeepSeek sent back something unexpected, so the plain "
                      "checks were used on their own.")

    out = []
    for item in parsed["terms"][:25]:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        definition = str(item.get("definition", "")).strip()
        if not term or len(term) > 70 or len(term) < 3:
            continue
        out.append((term, definition))

    reason = None
    if truncated:
        reason = ("The chapter was long, so only the first part of it was sent "
                  "to DeepSeek.")
    return out, reason


def save_key(key):
    """Store the key in this Mac's Keychain.

    Returns where it went, in words the author can read back in Settings.
    """
    if not keychain.save(keychain.ACCOUNT_DEEPSEEK, key):
        raise RuntimeError(
            "The key could not be stored in this Mac's Keychain, so it was not "
            "saved. Try again, and allow the Keychain prompt if one appears."
        )
    # Any older copy on disk is now redundant.
    for p in KEY_PATHS:
        try:
            os.remove(p)
        except OSError:
            pass
    return "this Mac's Keychain"
