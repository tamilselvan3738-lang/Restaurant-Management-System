from django import template
from urllib.parse import urlencode

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Appends or replaces query parameters in the current request GET dictionary.
    Preserves all active filters, searches, and sort keys across page transitions.
    """
    request = context.get('request')
    if not request:
        return '?' + urlencode(kwargs)
    
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == '':
            query.pop(key, None)
        else:
            query[key] = str(value)
            
    encoded = query.urlencode()
    return f"?{encoded}" if encoded else "?"
