from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TuiTheme:
    app_header: int = 0
    pane_active: int = 0
    pane_inactive: int = 0
    divider: int = 0
    footer: int = 0
    selection: int = 0
    user_header: int = 0
    assistant_header: int = 0
    assistant_final_header: int = 0
    status_muted: int = 0
    status_error: int = 0
    tool_header: int = 0
    code: int = 0


def build_curses_theme(curses: object) -> TuiTheme:
    """Build semantic curses attributes while keeping terminals without color usable."""
    reverse = attr(curses, "A_REVERSE")
    bold = attr(curses, "A_BOLD")
    dim = attr(curses, "A_DIM")
    base = TuiTheme(
        app_header=reverse,
        pane_active=reverse,
        pane_inactive=bold,
        divider=dim,
        footer=reverse,
        selection=reverse,
        user_header=bold,
        assistant_header=bold,
        assistant_final_header=bold,
        status_muted=dim,
        status_error=bold,
        tool_header=bold,
        code=dim,
    )
    if not has_color(curses):
        return base

    pairs = {
        "user": 1,
        "assistant": 2,
        "final": 3,
        "tool": 4,
        "error": 5,
        "muted": 6,
        "code": 7,
    }
    try:
        getattr(curses, "start_color")()
        use_default = getattr(curses, "use_default_colors", None)
        if callable(use_default):
            use_default()
        init_pair(curses, pairs["user"], "COLOR_CYAN")
        init_pair(curses, pairs["assistant"], "COLOR_BLUE")
        init_pair(curses, pairs["final"], "COLOR_GREEN")
        init_pair(curses, pairs["tool"], "COLOR_YELLOW")
        init_pair(curses, pairs["error"], "COLOR_RED")
        init_pair(curses, pairs["muted"], "COLOR_WHITE")
        init_pair(curses, pairs["code"], "COLOR_MAGENTA")
    except Exception:
        return base

    return TuiTheme(
        app_header=base.app_header,
        pane_active=base.pane_active,
        pane_inactive=base.pane_inactive,
        divider=base.divider,
        footer=base.footer,
        selection=base.selection,
        user_header=bold | color_pair(curses, pairs["user"]),
        assistant_header=bold | color_pair(curses, pairs["assistant"]),
        assistant_final_header=bold | color_pair(curses, pairs["final"]),
        status_muted=dim | color_pair(curses, pairs["muted"]),
        status_error=bold | color_pair(curses, pairs["error"]),
        tool_header=bold | color_pair(curses, pairs["tool"]),
        code=color_pair(curses, pairs["code"]),
    )


def attr(curses: object, name: str) -> int:
    value = getattr(curses, name, 0)
    return int(value) if isinstance(value, int) else 0


def has_color(curses: object) -> bool:
    has_colors = getattr(curses, "has_colors", None)
    if not callable(has_colors):
        return False
    try:
        return bool(has_colors())
    except Exception:
        return False


def init_pair(curses: object, pair: int, color_name: str) -> None:
    foreground = attr(curses, color_name)
    getattr(curses, "init_pair")(pair, foreground, -1)


def color_pair(curses: object, pair: int) -> int:
    value = getattr(curses, "color_pair")(pair)
    return int(value) if isinstance(value, int) else 0
