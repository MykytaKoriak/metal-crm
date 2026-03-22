from django import template


register = template.Library()


@register.filter
def user_display(user):
    if not user:
        return "—"

    try:
        profile = getattr(user, "profile", None)
        display_name = getattr(profile, "display_name", "")
    except Exception:
        display_name = ""

    try:
        full_name = user.get_full_name()
    except Exception:
        full_name = ""

    return display_name or full_name or getattr(user, "email", "") or getattr(user, "username", "") or str(user.pk)
