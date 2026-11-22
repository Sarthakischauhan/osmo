"""What the agent sees of a page and how it presses keys: its text, its controls, sensitive fields, screencast frames."""

from __future__ import annotations

from symphony_server.browser_cdp_models import AxNode, DescribedNode
from symphony_server.cdp import BrowserError

# The media type of a frame, from the start of its base64 data (Chrome's default is JPEG).
FRAME_MEDIA_TYPES = (("/9j/", "image/jpeg"), ("iVBORw0KGgo", "image/png"), ("UklGR", "image/webp"))
# The keyCode of Enter (what a DOM KeyboardEvent calls it, and CDP's windowsVirtualKeyCode).
ENTER_KEY_CODE = 13
READ_EXPRESSION = "({url: location.href, title: document.title, text: document.body ? document.body.innerText : ''})"
# The keys browser_press may press: DOM `key` -> (`code`, keyCode).
PRESSABLE_KEYS: dict[str, tuple[str, int]] = {
    "Enter": ("Enter", ENTER_KEY_CODE),
    "Escape": ("Escape", 27),
    "Tab": ("Tab", 9),
    "Backspace": ("Backspace", 8),
    "Space": ("Space", 32),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
    "PageUp": ("PageUp", 33),
    "PageDown": ("PageDown", 34),
    "Home": ("Home", 36),
    "End": ("End", 35),
}
# Input `autocomplete` tokens of secrets (besides type=password), and what browser_read calls them.
SENSITIVE_AUTOCOMPLETE = {
    "current-password": "password",
    "new-password": "password",
    "one-time-code": "one-time code",
    "cc-number": "card",
    "cc-csc": "card",
    "cc-exp": "card",
    "cc-exp-month": "card",
    "cc-exp-year": "card",
}
# The roles of nodes that can hold typed text (the ones checked for being sensitive).
TEXT_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})
SELECT_FUNCTION = """function (wanted) {
  if (!(this instanceof HTMLSelectElement)) return {error: 'not a dropdown (select element)'};
  const option = Array.from(this.options).find(o => o.value === wanted || o.label.trim() === wanted || o.text.trim() === wanted);
  if (!option) return {error: 'no option ' + JSON.stringify(wanted), options: Array.from(this.options).map(o => o.label.trim()).slice(0, 50)};
  this.value = option.value;
  this.dispatchEvent(new Event('input', {bubbles: true}));
  this.dispatchEvent(new Event('change', {bubbles: true}));
  return {selected: option.label.trim()};
}"""


def sensitive_kind(node: DescribedNode) -> str | None:
    """What secret an input holds ("password", "one-time code", "card"), or None for an ordinary field."""
    attributes = dict(zip(node.attributes[0::2], node.attributes[1::2], strict=False))
    if node.nodeName.upper() == "INPUT" and attributes.get("type", "").lower() == "password":
        return "password"
    tokens = attributes.get("autocomplete", "").lower().split()
    return next((SENSITIVE_AUTOCOMPLETE[token] for token in tokens if token in SENSITIVE_AUTOCOMPLETE), None)


def frame_data_url(data: str) -> str:
    """A screencast frame as a data: URL, typed by its image format; BrowserError for a format Osmo does not know."""
    media_type = next((kind for prefix, kind in FRAME_MEDIA_TYPES if data.startswith(prefix)), None)
    if media_type is None:
        raise BrowserError("the browser sent a screencast frame in an unknown image format")
    return f"data:{media_type};base64,{data}"


def role_of(node: AxNode) -> str:
    """An accessibility node's role ("" when it has none)."""
    return str(node.role.value) if node.role else ""


def is_focusable(node: AxNode) -> bool:
    """Whether an accessibility node can take focus (a control the agent may act on)."""
    return any(prop.name == "focusable" and prop.value.value is True for prop in node.properties)
