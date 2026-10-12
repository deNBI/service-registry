from django import template

from apps.submissions.markdown_render import (
    render_submission_description,
    submission_description_snippet,
)

register = template.Library()


@register.filter
def md_description(submission):
    """Rendered (or plain, when flag off) description HTML for the list view."""
    return render_submission_description(submission)


@register.filter
def md_snippet(submission):
    """Plain-text snippet for compact cards (cached; plain when flag off)."""
    return submission_description_snippet(submission)
