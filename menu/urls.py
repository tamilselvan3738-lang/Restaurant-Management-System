from django.urls import path
from . import views

app_name = 'menu'

urlpatterns = [
    path('', views.menu_list, name='list'),
    path('item/add/', views.menu_item_create, name='item_create'),
    path('item/<int:item_id>/edit/', views.menu_item_edit, name='item_edit'),
    path('item/<int:item_id>/delete/', views.menu_item_delete, name='item_delete'),
    path('item/<int:item_id>/toggle-availability/', views.menu_item_toggle_availability, name='item_toggle_availability'),
    path('categories/', views.category_list, name='categories'),
    path('categories/<int:category_id>/toggle-status/', views.category_toggle_status, name='category_toggle_status'),
    path('categories/<int:category_id>/edit/', views.category_edit, name='category_edit'),
    path('categories/<int:category_id>/delete/', views.category_delete, name='category_delete'),
    path('inventory/', views.inventory_list, name='inventory'),
    path('inventory/add/', views.ingredient_create, name='ingredient_create'),
    path('inventory/<int:ingredient_id>/restock/', views.inventory_restock, name='inventory_restock'),
    # Food Combos
    path('combos/', views.combo_list, name='combo_list'),
    path('combos/add/', views.combo_create, name='combo_create'),
    path('combos/<int:combo_id>/edit/', views.combo_edit, name='combo_edit'),
    path('combos/<int:combo_id>/toggle/', views.combo_toggle_status, name='combo_toggle_status'),
    path('combos/<int:combo_id>/delete/', views.combo_delete, name='combo_delete'),
    path('combos/sales/', views.combo_sales_report, name='combo_sales_report'),
]
