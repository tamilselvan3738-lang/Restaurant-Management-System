from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


def make_tenant_key(tenant, key_name):
    """
    Constructs a strictly isolated cache key incorporating the tenant ID.
    Never allows cross-tenant cache collisions or data leakage.
    """
    tenant_id = getattr(tenant, 'id', None) or 'global'
    return f"tenant:{tenant_id}:{key_name}"


def get_cached_tenant_config(restaurant):
    """Retrieves or caches RestaurantConfig for the given restaurant tenant."""
    from superadmin.models import RestaurantConfig
    key = make_tenant_key(restaurant, 'config')
    cfg = cache.get(key)
    if cfg is None:
        cfg = RestaurantConfig.get_config(restaurant=restaurant)
        cache.set(key, cfg, timeout=600)
    return cfg


def invalidate_tenant_config(restaurant):
    """Evicts cached configuration for the given restaurant tenant."""
    key = make_tenant_key(restaurant, 'config')
    cache.delete(key)


def get_cached_active_categories(restaurant):
    """Retrieves or caches the list of active categories for this restaurant."""
    from menu.models import Category
    key = make_tenant_key(restaurant, 'active_categories')
    cats = cache.get(key)
    if cats is None:
        if restaurant:
            cats = list(Category.objects.filter(restaurant=restaurant, is_active=True).order_by('display_order', 'name'))
        else:
            cats = list(Category.objects.filter(is_active=True).order_by('display_order', 'name'))
        cache.set(key, cats, timeout=300)
    return cats


def invalidate_tenant_menu(restaurant):
    """Evicts active category and menu caches for this tenant."""
    cache.delete(make_tenant_key(restaurant, 'active_categories'))
    cache.delete(make_tenant_key(restaurant, 'menu_list'))


@receiver([post_save, post_delete], sender='menu.Category')
def on_category_change(sender, instance, **kwargs):
    rest = getattr(instance, 'restaurant', None)
    invalidate_tenant_menu(rest)


@receiver([post_save, post_delete], sender='menu.MenuItem')
def on_menu_item_change(sender, instance, **kwargs):
    rest = getattr(instance, 'restaurant', None)
    invalidate_tenant_menu(rest)


@receiver([post_save, post_delete], sender='superadmin.RestaurantConfig')
def on_config_change(sender, instance, **kwargs):
    rest = getattr(instance, 'restaurant', None)
    invalidate_tenant_config(rest)
