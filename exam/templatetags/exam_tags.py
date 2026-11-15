from django import template

from bank.constants import LETTERS

register = template.Library()


@register.filter
def letter(index: int) -> str:
    """0 → « A », 1 → « B »…"""
    return LETTERS[index]


@register.filter
def minutes_seconds(seconds: int | None) -> str:
    """Durée lisible : « 12 min 05 s »."""
    if seconds is None:
        return ""
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"
