from django.contrib import admin
from .models import Category, DietaryTag, MenuItem, Ingredient, MenuItemIngredient

class MenuItemIngredientInline(admin.TabularInline):
    model = MenuItemIngredient
    extra = 1

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon', 'display_order', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('display_order', 'is_active')

@admin.register(DietaryTag)
class DietaryTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'badge_color')

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'cost_price', 'preparation_time', 'is_available', 'is_special')
    list_filter = ('category', 'is_available', 'is_special', 'dietary_tags')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('price', 'is_available', 'is_special')
    inlines = [MenuItemIngredientInline]

@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ('name', 'current_stock', 'unit', 'low_stock_threshold', 'cost_per_unit', 'last_restocked')
    list_editable = ('current_stock', 'low_stock_threshold')
    search_fields = ('name',)
