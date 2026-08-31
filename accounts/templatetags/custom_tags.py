from django import template

register = template.Library()

@register.filter
def dict_get(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key, "")
    return ""