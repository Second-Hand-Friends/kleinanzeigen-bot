# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Mandatory link sanitation for eBay descriptions.

Our publishing requirement is that eBay descriptions contain no links at
all. This is deliberately stricter than eBay's own link policy, which has
exceptions: instead of tracking exactly which links eBay tolerates, every
link is removed. The whole description text (including caller-supplied
prefix/suffix affixes) is therefore sanitized into safe plain text before
publishing. [sanitize_description()][kleinanzeigen_bot.ebay_description.sanitize_description]
removes written URLs (http/https/ftp, protocol-relative "//host", www.,
recognizable bare domains including IDNs), HTML anchors, Markdown links
(inline, reference, autolink), and active HTML content
(script/style/iframe/object/embed), while preserving meaningful link
labels and paragraph breaks. The input string is never modified; a
sanitized copy plus the link removal count is returned.

Decisions and limitations, pinned by tests in
`tests/unit/test_ebay_description.py`:

- HTML entities are decoded first, so entity-encoded links
  (`https&#58;&#47;&#47;...`, double-encoded input) are removed as well.
- A link label that is itself a URL is dropped with the link; a label
  that merely contains a URL inside other words is kept and the embedded
  URL is removed separately (counted separately).
- Each inline link, autolink, HTML anchor with `href`, Markdown
  reference usage, and orphan reference definition counts as one removal.
- Email addresses (`user@example.com`, also `(at)`-obfuscated) are kept:
  they are contact data, not links, and mangling them loses information.
  Explicit `mailto:` links are removed entirely instead, since the scheme
  marks unambiguous link intent.
- Truncated input is handled: unclosed trailing active elements and tag
  fragments at the end of the input are dropped with the active content.
- Markdown footnote definitions (`[^1]: ...`) are kept literally.
- Bare-domain detection is heuristic (Unicode-aware labels, lettered TLD
  of 2-24 chars), so file names such as `manual.pdf` may be removed as
  well; per our remove-all-links requirement, over-removal is preferred
  over a missed link. Digit TLDs (`USB3.0`, `3.12`) and single-letter TLDs
  (`z.B.`) are never treated as domains.
"""

__all__ = ["sanitize_description"]

import html
import re
from typing import Final

# Block-level elements whose content is active or embeddable and is therefore
# dropped entirely, including everything between the opening and closing tags.
_ACTIVE_BLOCK_RE:Final = re.compile(
    r"<\s*(script|style|iframe|object|embed)\b[^<>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Unclosed trailing active element (length-truncated input): per HTML parsing
# rules its content runs to the end of the input, so it is dropped as a whole.
# Void <embed> is excluded here (it cannot swallow text) and is stripped as
# a plain tag instead, keeping the text that follows it.
_UNCLOSED_ACTIVE_RE:Final = re.compile(
    r"<\s*(script|style|iframe|object)\b[^<>]*>.*",
    re.IGNORECASE | re.DOTALL,
)
# Truncated tag fragment at the very end of the input ("Buy now <b").
_TRAILING_TAG_FRAGMENT_RE:Final = re.compile(r"<[A-Za-z/!][^<>\n]*\n?\Z")

_HTML_COMMENT_RE:Final = re.compile(r"<!--.*?-->", re.DOTALL)

_HTML_ANCHOR_RE:Final = re.compile(
    r"<\s*a\b(?P<attrs>[^<>]*?)>(?P<label>.*?)<\s*/\s*a\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_ANCHOR_EMPTY_RE:Final = re.compile(r"<\s*a\b(?P<attrs>[^<>]*?)/\s*>", re.IGNORECASE)
_HTML_HREF_RE:Final = re.compile(r"href\s*=", re.IGNORECASE)

# Block tags become paragraph breaks; every other tag is stripped inline.
_HTML_BLOCK_TAG_RE:Final = re.compile(
    r"</?\s*(?:p|div|h[1-6]|li|ul|ol|dl|dt|dd|tr|table|blockquote|section|article|header|footer|main|aside|nav|pre|hr|br)\b[^<>]*?/?>",
    re.IGNORECASE,
)
# Strict tag-name match so "<user@example.com>" or "a < b" survive as text.
_HTML_TAG_RE:Final = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")

_MD_DEFINITION_RE:Final = re.compile(
    r"""^[ \t]{0,3}\[(?P<ref>[^\]\n]+)\][ \t]*:[ \t]*<?[^\s<>\]]+>?[ \t]*(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\))?[ \t]*$""",
    re.MULTILINE,
)
_MD_INLINE_RE:Final = re.compile(
    r"""(?P<image>!?)\[(?P<label>[^\]]*?)\]\(\s*<?(?P<target>[^)\s]+)?\s*(?:"[^"]*"|'[^']*'|\([^)]*\))?\s*\)""",
    re.DOTALL,
)
_MD_REF_FULL_RE:Final = re.compile(r"(?P<image>!?)\[(?P<label>[^\]\n]*?)\]\[(?P<ref>[^\]\n]*?)\]")
_MD_SHORTCUT_RE:Final = re.compile(r"(?<!\])(?P<image>!?)\[(?P<label>[^\]\n]+?)\](?![\[(])")
# DNS host fragments. The classes are Unicode-aware ([^\W...] also matches
# non-ASCII letters), so internationalized domains ("bücher.de") match.
# The lettered TLD keeps version numbers ("USB3.0", "3.12") and
# single-letter abbreviations ("z.B.") intact.
_DNS_LABEL:Final = r"[^\W_](?:[^\W_-]{0,61}[^\W_])?"
_DNS_TLD:Final = r"[^\W\d_]{2,24}"
_DNS_HOST:Final = rf"(?:{_DNS_LABEL}\.)+{_DNS_TLD}"
_PORT:Final = r"(?::\d{1,5})?"
_PATH:Final = r"(?:/[^\s<>\"]*)?"
# Single-label hosts ("//localhost:3000/x") need a port or path to count.
_SINGLE_HOST:Final = rf"{_DNS_LABEL}(?:(?::\d{{1,5}}){_PATH}|/+[^\s<>\"]+)"

_MD_AUTOLINK_RE:Final = re.compile(
    rf"<(?:(?:https?|ftp)://[^<>\s]+|mailto:[^<>\s]+|www\.[^<>\s]+|//www\.[^<>\s]+|//{_DNS_HOST}(?::\d{{1,5}})?(?:/[^<>\s]*)?)>",
    re.IGNORECASE,
)

# Written-out URLs. The lookbehind keeps emails ("user@example.com") and
# path remainders ("a/b", "/www.example.com") intact.
_SCHEME_URL_RE:Final = re.compile(r"(?:https?|ftp)://[^\s<>\"]+", re.IGNORECASE)
# Explicit "mailto:" links are removed entirely; unlike a bare email address,
# the scheme marks unambiguous link intent.
_MAILTO_RE:Final = re.compile(r"mailto:[^\s<>\"]+", re.IGNORECASE)
_PROTOCOL_RELATIVE_RE:Final = re.compile(
    rf"(?<![\w@/.])//(?:www\.[^\s<>\"]+|{_DNS_HOST}{_PORT}{_PATH}|{_SINGLE_HOST})",
    re.IGNORECASE,
)
_WWW_URL_RE:Final = re.compile(r"(?<![\w@/.])www\.[^\s<>\"]+", re.IGNORECASE)
_BARE_DOMAIN_RE:Final = re.compile(rf"(?<![\w@/.]){_DNS_HOST}{_PORT}{_PATH}")

# Full-match variants (without boundary lookbehind) for URL-like link labels.
_LABEL_SCHEME_RE:Final = re.compile(r"(?:https?|ftp)://[^\s<>\"]+", re.IGNORECASE)
_LABEL_MAILTO_RE:Final = re.compile(r"mailto:[^\s<>\"]+", re.IGNORECASE)
_LABEL_PROTOCOL_RELATIVE_RE:Final = re.compile(
    rf"//(?:www\.[^\s<>\"]+|{_DNS_HOST}{_PORT}{_PATH}|{_SINGLE_HOST})",
    re.IGNORECASE,
)
_LABEL_WWW_RE:Final = re.compile(r"www\.[^\s<>\"]+", re.IGNORECASE)
_LABEL_DOMAIN_RE:Final = re.compile(rf"{_DNS_HOST}{_PORT}{_PATH}")

# Punctuation that may follow a URL in running text; it is kept, not removed.
_TRAILING_PUNCT:Final = frozenset((".", ",", ";", ":", "!", "?", ")", "]", "'", '"'))
_WS_RUN_RE:Final = re.compile(r"[ \t\f\v]+")
_REF_WS_RE:Final = re.compile(r"\s+")
_BLANK_RUN_RE:Final = re.compile(r"\n{3,}")


def _normalize_ref(ref:str) -> str:
    """Normalize a Markdown reference id for case-insensitive comparison."""
    return _REF_WS_RE.sub(" ", ref).strip().lower()


def _collapse_whitespace(value:str) -> str:
    """Collapse all whitespace runs; rendered Markdown/HTML ignores layout."""
    return _REF_WS_RE.sub(" ", value).strip()


def _split_trailing_punct(url:str) -> tuple[str, str]:
    """Split punctuation following a URL (kept in the text) from the URL itself."""
    end = len(url)
    while end > 0 and url[end - 1] in _TRAILING_PUNCT:
        # Keep a closing paren that balances an opening paren inside the URL.
        if url[end - 1] == ")" and url.count("(", 0, end) >= url.count(")", 0, end):
            break
        end -= 1
    return url[:end], url[end:]


def _is_url_label(label:str) -> bool:
    """Check whether a link label is just a URL (such labels are dropped)."""
    candidate = label.strip().strip("<>\"'").strip()
    candidate, _ = _split_trailing_punct(candidate)
    candidate = candidate.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return False
    return (
        _LABEL_SCHEME_RE.fullmatch(candidate) is not None
        or _LABEL_MAILTO_RE.fullmatch(candidate) is not None
        or _LABEL_PROTOCOL_RELATIVE_RE.fullmatch(candidate) is not None
        or _LABEL_WWW_RE.fullmatch(candidate) is not None
        or _LABEL_DOMAIN_RE.fullmatch(candidate) is not None
    )


def _is_obfuscated_address(text:str, start:int) -> bool:
    """Check for an "(at)" email obfuscation directly before a domain match."""
    tail = text[max(0, start - 6):start]
    return tail.lower().rstrip().endswith("(at)")


def _normalize_text(value:str) -> str:
    """Collapse redundant whitespace while preserving paragraph breaks."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RUN_RE.sub(" ", line).strip() for line in value.split("\n")]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


def sanitize_description(text:str) -> tuple[str, int]:
    """Remove every link from a description, returning safe plain text.

    Args:
        text: Complete description, including caller-supplied affixes.

    Returns:
        Tuple of the sanitized plain text and the number of removed link
        occurrences. The input string is never modified.

    Examples:
    >>> sanitize_description("No links here.")
    ('No links here.', 0)
    >>> sanitize_description("Visit https://example.com today.")
    ('Visit today.', 1)
    >>> sanitize_description("[Sale](https://example.com/shop) ends soon!")
    ('Sale ends soon!', 1)
    >>> sanitize_description('<a href="https://example.com">https://example.com</a>')
    ('', 1)
    """
    removed = 0

    # Decode entities first so that encoded links ("https&#58;&#47;&#47;...")
    # are detected like their plain-text equivalents. Repeat to catch
    # double-encoded input, then work only on the decoded copy.
    decoded = text
    for _ in range(5):
        once = html.unescape(decoded)
        if once == decoded:
            break
        decoded = once
    work = decoded.replace("\r\n", "\n").replace("\r", "\n")

    # Drop comments first so that tag-like text inside them cannot confuse
    # the active-content rules. Active/embedded content is dropped entirely
    # (not counted as links); unclosed trailing elements and tag fragments
    # from truncated input are dropped with it.
    work = _HTML_COMMENT_RE.sub("", work)
    work = _ACTIVE_BLOCK_RE.sub("", work)
    work = _UNCLOSED_ACTIVE_RE.sub("", work)
    work = _TRAILING_TAG_FRAGMENT_RE.sub("", work)

    def _replace_anchor(match:re.Match[str]) -> str:
        nonlocal removed
        if not _HTML_HREF_RE.search(match.group("attrs")):
            # Named anchor without a target: keep the inner text.
            return match.group("label")
        removed += 1
        label = _collapse_whitespace(_HTML_TAG_RE.sub("", match.group("label")))
        if not label or _is_url_label(label):
            return ""
        return label

    def _replace_empty_anchor(match:re.Match[str]) -> str:
        nonlocal removed
        if _HTML_HREF_RE.search(match.group("attrs")):
            removed += 1
        return ""

    work = _HTML_ANCHOR_RE.sub(_replace_anchor, work)
    work = _HTML_ANCHOR_EMPTY_RE.sub(_replace_empty_anchor, work)

    # Collect reference ids before removing the definition lines so that
    # usages ("[label][ref]", "[label][]", "[label]") can be resolved.
    defined_refs:set[str] = set()
    for definition in _MD_DEFINITION_RE.finditer(work):
        ref = _normalize_ref(definition.group("ref"))
        # Footnote definitions ("[^1]: ...") are not links; keep them.
        if not ref.startswith("^"):
            defined_refs.add(ref)
    used_refs:set[str] = set()

    def _replace_definition(match:re.Match[str]) -> str:
        if _normalize_ref(match.group("ref")).startswith("^"):
            return match.group(0)
        return ""

    work = _MD_DEFINITION_RE.sub(_replace_definition, work)

    def _replace_inline(match:re.Match[str]) -> str:
        nonlocal removed
        if match.group("target") is None:
            # Degenerate "[label]()": no target to remove, keep the label.
            return match.group("label")
        removed += 1
        label = _collapse_whitespace(_HTML_TAG_RE.sub("", match.group("label")))
        if not label or _is_url_label(label):
            return ""
        return label

    work = _MD_INLINE_RE.sub(_replace_inline, work)

    def _replace_reference(match:re.Match[str], *, shortcut:bool) -> str:
        nonlocal removed
        ref = match.group("label") if shortcut else (match.group("ref") or match.group("label"))
        normalized = _normalize_ref(ref)
        if normalized.startswith("^") or normalized not in defined_refs:
            # Undefined reference: not a link, keep the literal text.
            return match.group(0)
        removed += 1
        used_refs.add(normalized)
        label = _collapse_whitespace(_HTML_TAG_RE.sub("", match.group("label")))
        if not label or _is_url_label(label):
            return ""
        return label

    work = _MD_REF_FULL_RE.sub(lambda match:_replace_reference(match, shortcut = False), work)
    work = _MD_SHORTCUT_RE.sub(lambda match:_replace_reference(match, shortcut = True), work)
    # Reference definitions without any usage still contained a link target.
    removed += len(defined_refs - used_refs)

    def _replace_autolink(_match:re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        return ""

    work = _MD_AUTOLINK_RE.sub(_replace_autolink, work)

    # Block structure becomes plain-text paragraphs; inline tags vanish.
    work = _HTML_BLOCK_TAG_RE.sub("\n", work)
    work = _HTML_TAG_RE.sub("", work)

    def _replace_bare_url(match:re.Match[str]) -> str:
        nonlocal removed
        if _is_obfuscated_address(match.string, match.start()):
            return match.group(0)
        removed += 1
        _url, trailing = _split_trailing_punct(match.group(0))
        return trailing

    work = _SCHEME_URL_RE.sub(_replace_bare_url, work)
    work = _MAILTO_RE.sub(_replace_bare_url, work)
    work = _PROTOCOL_RELATIVE_RE.sub(_replace_bare_url, work)
    work = _WWW_URL_RE.sub(_replace_bare_url, work)
    work = _BARE_DOMAIN_RE.sub(_replace_bare_url, work)

    return _normalize_text(work), removed
