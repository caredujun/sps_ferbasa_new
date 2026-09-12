from django import template

register = template.Library()


@register.filter(name='has_group')
def has_group(user, group_name):
    """
    Uso no template: {{ user|has_group:"Nome do Grupo" }}
    Retorna True/False conforme o usuário pertence ou não ao grupo.
    """
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=group_name).exists()