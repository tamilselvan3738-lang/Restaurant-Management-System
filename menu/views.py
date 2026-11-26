from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.db import transaction
from django.views.decorators.http import require_POST
from django.http import Http404

from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import (
    get_current_restaurant,
    is_impersonating_restaurant,
    get_tenant_object_or_404,
    filter_by_tenant,
)
from .models import MenuItem, Category, Ingredient, DietaryTag, FoodCombo, FoodComboItem
from .forms import MenuItemForm, CategoryForm, IngredientForm, StockUpdateForm, FoodComboForm
from orders.models import OrderItem, Order


@login_required
def menu_list(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    category_slug = request.GET.get('category')
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'active')

    # Categories scoped strictly to active tenant
    if restaurant:
        categories = Category.objects.filter(restaurant=restaurant, is_active=True)
        items_base = MenuItem.objects.filter(restaurant=restaurant)
    elif request.user.is_superuser and not is_impersonating:
        categories = Category.objects.filter(is_active=True)
        items_base = MenuItem.objects.all()
    else:
        categories = Category.objects.none()
        items_base = MenuItem.objects.none()

    # Base query for menu items
    if status_filter == 'inactive' and request.user.is_admin_or_manager:
        items = items_base.filter(
            Q(is_available=False) | Q(category__is_active=False)
        )
    elif status_filter == 'all' and request.user.is_admin_or_manager:
        items = items_base
    else:
        # Default view: Only active items belonging to active categories
        status_filter = 'active'
        items = items_base.filter(
            is_available=True,
            category__is_active=True
        )

    # Total counts for status badges
    active_count = items_base.filter(is_available=True, category__is_active=True).count()
    inactive_count = items_base.filter(
        Q(is_available=False) | Q(category__is_active=False)
    ).count()

    items = items.select_related('category').prefetch_related('dietary_tags')

    if category_slug:
        items = items.filter(category__slug=category_slug)

    if search_query:
        items = items.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(category__name__icontains=search_query)
        )

    paginator = Paginator(items, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'categories': categories,
        'items': page_obj,
        'page_obj': page_obj,
        'selected_category': category_slug,
        'search_query': search_query,
        'status_filter': status_filter,
        'active_count': active_count,
        'inactive_count': inactive_count,
    }
    return render(request, 'menu/menu_list.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def menu_item_create(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)

    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, restaurant=restaurant)
        if form.is_valid():
            item = form.save()
            messages.success(request, f"Menu item '{item.name}' added successfully.")
            return redirect('menu:list')
        else:
            messages.error(request, "Please check the form for errors and try again.")
    else:
        form = MenuItemForm(restaurant=restaurant)

    return render(request, 'menu/item_form.html', {'form': form, 'title': 'Add Menu Item'})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def menu_item_edit(request, item_id):
    item = get_tenant_object_or_404(MenuItem, request, id=item_id)
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request) or item.restaurant

    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, instance=item, restaurant=restaurant)
        if form.is_valid():
            item = form.save()
            messages.success(request, f"Menu item '{item.name}' updated successfully.")
            return redirect('menu:list')
        else:
            messages.error(request, "Please check the form for errors and try again.")
    else:
        form = MenuItemForm(instance=item, restaurant=restaurant)

    return render(request, 'menu/item_form.html', {'form': form, 'title': f'Edit: {item.name}', 'item': item})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def menu_item_delete(request, item_id):
    item = get_tenant_object_or_404(MenuItem, request, id=item_id)
    if request.method == 'POST':
        name = item.name
        has_active_orders = OrderItem.objects.filter(
            menu_item=item,
            order__status__in=['PENDING', 'ACCEPTED', 'PREPARING', 'READY', 'SERVED']
        ).exists()

        if has_active_orders:
            messages.error(
                request,
                f"Cannot delete '{name}' because it is part of active kitchen/table orders. Please complete or cancel those orders first, or deactivate the dish instead."
            )
            return redirect('menu:list')

        item.delete()
        messages.success(request, f"Dish '{name}' has been permanently deleted.")
    return redirect('menu:list')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.CASHIER])
def menu_item_toggle_availability(request, item_id):
    item = get_tenant_object_or_404(MenuItem, request, id=item_id)
    item.is_available = not item.is_available
    item.save(update_fields=['is_available'])
    if item.is_available:
        messages.success(request, f"'{item.name}' is now Active and visible on the menu and search.")
    else:
        messages.warning(request, f"'{item.name}' has been Deactivated and hidden from the active menu and search.")
    return redirect(request.META.get('HTTP_REFERER', 'menu:list'))


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.CASHIER])
def inventory_list(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    if restaurant:
        ingredients = Ingredient.objects.filter(restaurant=restaurant).order_by('name')
    elif request.user.is_superuser and not is_impersonating:
        ingredients = Ingredient.objects.all().order_by('name')
    else:
        ingredients = Ingredient.objects.none()

    low_stock_count = sum(1 for i in ingredients if i.is_low_stock)
    return render(request, 'menu/inventory_list.html', {
        'ingredients': ingredients,
        'low_stock_count': low_stock_count
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def ingredient_create(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)

    if request.method == 'POST':
        form = IngredientForm(request.POST, restaurant=restaurant)
        if form.is_valid():
            ing = form.save()
            messages.success(request, f"Ingredient '{ing.name}' added to inventory.")
            return redirect('menu:inventory')
    else:
        form = IngredientForm(restaurant=restaurant)

    return render(request, 'menu/ingredient_form.html', {'form': form, 'title': 'Add Inventory Ingredient'})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.KITCHEN, User.Role.CASHIER])
def inventory_restock(request, ingredient_id):
    ingredient = get_tenant_object_or_404(Ingredient, request, id=ingredient_id)
    if request.method == 'POST':
        form = StockUpdateForm(request.POST)
        if form.is_valid():
            qty = form.cleaned_data['quantity_to_add']
            ingredient.current_stock += qty
            ingredient.save(update_fields=['current_stock', 'last_restocked'])
            messages.success(request, f"Restocked {qty} {ingredient.unit} of '{ingredient.name}'. New stock: {ingredient.current_stock} {ingredient.unit}")
            return redirect('menu:inventory')
    else:
        form = StockUpdateForm()

    return render(request, 'menu/restock_modal.html', {'form': form, 'ingredient': ingredient})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def category_list(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    if restaurant:
        categories = Category.objects.filter(restaurant=restaurant).annotate(dish_count=Count('items')).order_by('display_order', 'name')
    elif request.user.is_superuser and not is_impersonating:
        categories = Category.objects.annotate(dish_count=Count('items')).order_by('display_order', 'name')
    else:
        categories = Category.objects.none()

    if request.method == 'POST':
        form = CategoryForm(request.POST, restaurant=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, "New category added.")
            return redirect('menu:categories')
    else:
        form = CategoryForm(restaurant=restaurant)

    return render(request, 'menu/category_list.html', {'categories': categories, 'form': form})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def category_toggle_status(request, category_id):
    category = get_tenant_object_or_404(Category, request, id=category_id)
    category.is_active = not category.is_active
    category.save(update_fields=['is_active'])
    if category.is_active:
        messages.success(request, f"Category '{category.name}' is now Active. Its dishes are now visible in the menu and search.")
    else:
        messages.warning(request, f"Category '{category.name}' has been Deactivated. Its dishes are now hidden from the menu and search.")
    return redirect('menu:categories')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def category_edit(request, category_id):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    category = get_tenant_object_or_404(Category, request, id=category_id)

    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category, restaurant=restaurant)
        if form.is_valid():
            cat = form.save()
            status_text = "Active" if cat.is_active else "Disabled"
            messages.success(request, f"Category '{cat.name}' updated successfully ({status_text}).")
            return redirect('menu:categories')
    else:
        form = CategoryForm(instance=category, restaurant=restaurant)
    return render(request, 'menu/category_form.html', {'form': form, 'category': category})


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def category_delete(request, category_id):
    category = get_tenant_object_or_404(Category, request, id=category_id)
    if request.method == 'POST':
        name = category.name
        items_count = category.items.count()
        category.delete()
        messages.success(request, f"Category '{name}' deleted ({items_count} dish{items_count|pluralize:'es'} removed).")
    return redirect('menu:categories')


# ==============================================================================
# FUNCTIONALITY B: CUSTOM FOOD COMBOS MANAGEMENT (ADMIN & CASHIER)
# ==============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def combo_list(request):
    """Displays custom food combos strictly for the active restaurant tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    is_impersonating = getattr(request, 'is_impersonating', False) or is_impersonating_restaurant(request)

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all')

    if restaurant:
        combos = FoodCombo.objects.filter(restaurant=restaurant).prefetch_related('items__menu_item')
    elif request.user.is_superuser and not is_impersonating:
        combos = FoodCombo.objects.prefetch_related('items__menu_item').all()
    else:
        combos = FoodCombo.objects.none()

    if status_filter == 'active':
        combos = combos.filter(is_available=True)
    elif status_filter == 'inactive':
        combos = combos.filter(is_available=False)

    if search_query:
        combos = combos.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(items__menu_item__name__icontains=search_query)
        ).distinct()

    total_count = combos.count()
    active_count = combos.filter(is_available=True).count()
    inactive_count = total_count - active_count

    return render(request, 'menu/combo_list.html', {
        'combos': combos,
        'search_query': search_query,
        'status_filter': status_filter,
        'total_count': total_count,
        'active_count': active_count,
        'inactive_count': inactive_count,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def combo_create(request):
    """Interactive combo builder scoped strictly to tenant menu items."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    if restaurant:
        available_items = MenuItem.objects.filter(restaurant=restaurant, is_available=True, category__is_active=True)
    else:
        available_items = MenuItem.objects.filter(is_available=True, category__is_active=True)

    if request.method == 'POST':
        form = FoodComboForm(request.POST, request.FILES, restaurant=restaurant)
        item_ids = request.POST.getlist('item_id[]')
        quantities = request.POST.getlist('quantity[]')

        selected_pairs = []
        seen_items = set()
        for idx, i_id in enumerate(item_ids):
            if i_id and i_id.isdigit():
                item_pk = int(i_id)
                qty = int(quantities[idx]) if idx < len(quantities) and quantities[idx].isdigit() else 1
                if qty > 0 and item_pk not in seen_items:
                    seen_items.add(item_pk)
                    selected_pairs.append((item_pk, qty))

        if len(selected_pairs) < 3:
            messages.error(request, "Validation Failed: A custom food combo must contain at least three (3) distinct food items.")
            return render(request, 'menu/combo_form.html', {
                'form': form,
                'available_items': available_items,
                'is_edit': False,
                'selected_pairs': selected_pairs,
            })

        if form.is_valid():
            with transaction.atomic():
                combo = form.save(commit=False)
                if restaurant:
                    combo.restaurant = restaurant
                combo.save()

                for item_pk, qty in selected_pairs:
                    menu_item = get_tenant_object_or_404(MenuItem, request, id=item_pk)
                    FoodComboItem.objects.create(
                        combo=combo,
                        menu_item=menu_item,
                        quantity=qty,
                        unit_price_snapshot=menu_item.price
                    )

                messages.success(
                    request,
                    f"Special Food Combo '{combo.name}' created successfully with {len(selected_pairs)} items! Selling price: ₹{combo.price}."
                )
                return redirect('menu:combo_list')
    else:
        form = FoodComboForm(initial={'is_available': True}, restaurant=restaurant)
        selected_pairs = []

    return render(request, 'menu/combo_form.html', {
        'form': form,
        'available_items': available_items,
        'is_edit': False,
        'selected_pairs': selected_pairs,
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def combo_edit(request, combo_id):
    """Edit existing custom combo details strictly scoped by tenant."""
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)
    combo = get_tenant_object_or_404(FoodCombo.objects.prefetch_related('items__menu_item'), request, id=combo_id)

    if restaurant:
        available_items = MenuItem.objects.filter(restaurant=restaurant, is_available=True, category__is_active=True)
    else:
        available_items = MenuItem.objects.filter(is_available=True, category__is_active=True)

    if request.method == 'POST':
        form = FoodComboForm(request.POST, request.FILES, instance=combo, restaurant=restaurant)
        item_ids = request.POST.getlist('item_id[]')
        quantities = request.POST.getlist('quantity[]')

        selected_pairs = []
        seen_items = set()
        for idx, i_id in enumerate(item_ids):
            if i_id and i_id.isdigit():
                item_pk = int(i_id)
                qty = int(quantities[idx]) if idx < len(quantities) and quantities[idx].isdigit() else 1
                if qty > 0 and item_pk not in seen_items:
                    seen_items.add(item_pk)
                    selected_pairs.append((item_pk, qty))

        if len(selected_pairs) < 3:
            messages.error(request, "Validation Failed: A custom food combo must contain at least three (3) distinct food items.")
            return render(request, 'menu/combo_form.html', {
                'form': form,
                'combo': combo,
                'available_items': available_items,
                'is_edit': True,
                'selected_pairs': selected_pairs,
            })

        if form.is_valid():
            with transaction.atomic():
                combo = form.save()
                combo.items.all().delete()
                for item_pk, qty in selected_pairs:
                    menu_item = get_tenant_object_or_404(MenuItem, request, id=item_pk)
                    FoodComboItem.objects.create(
                        combo=combo,
                        menu_item=menu_item,
                        quantity=qty,
                        unit_price_snapshot=menu_item.price
                    )

                messages.success(request, f"Combo '{combo.name}' updated successfully with {len(selected_pairs)} items.")
                return redirect('menu:combo_list')
    else:
        form = FoodComboForm(instance=combo, restaurant=restaurant)
        selected_pairs = [(it.menu_item.id, it.quantity) for it in combo.items.all()]

    return render(request, 'menu/combo_form.html', {
        'form': form,
        'combo': combo,
        'available_items': available_items,
        'is_edit': True,
        'selected_pairs': selected_pairs,
    })


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def combo_toggle_status(request, combo_id):
    combo = get_tenant_object_or_404(FoodCombo, request, id=combo_id)
    combo.is_available = not combo.is_available
    combo.save(update_fields=['is_available', 'updated_at'])

    status_str = "Active and visible in QR Menu" if combo.is_available else "Deactivated and hidden from customers"
    messages.success(request, f"Combo '{combo.name}' is now {status_str}.")
    return redirect('menu:combo_list')


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER])
def combo_delete(request, combo_id):
    combo = get_tenant_object_or_404(FoodCombo, request, id=combo_id)
    name = combo.name
    combo.delete()
    messages.success(request, f"Special Combo '{name}' has been deleted.")
    return redirect('menu:combo_list')


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def combo_sales_report(request):
    restaurant = getattr(request, 'restaurant', None) or get_current_restaurant(request)

    if restaurant:
        combo_orders = OrderItem.objects.filter(is_combo_header=True, combo__restaurant=restaurant).select_related('combo', 'order')
        combos = FoodCombo.objects.filter(restaurant=restaurant)
    else:
        combo_orders = OrderItem.objects.filter(is_combo_header=True).select_related('combo', 'order')
        combos = FoodCombo.objects.all()

    report_data = []
    total_combo_revenue = Decimal('0.00')
    total_units_sold = 0
    total_savings_delivered = Decimal('0.00')

    for c in combos:
        c_items = combo_orders.filter(combo=c)
        sold_qty = sum(item.quantity for item in c_items)
        revenue = sum(item.total_price for item in c_items)
        savings = c.savings_amount * sold_qty

        total_combo_revenue += revenue
        total_units_sold += sold_qty
        total_savings_delivered += savings

        report_data.append({
            'combo': c,
            'sold_qty': sold_qty,
            'revenue': revenue,
            'savings': savings,
            'orders_count': c_items.count(),
        })

    return render(request, 'menu/combo_sales_report.html', {
        'report_data': report_data,
        'total_combo_revenue': total_combo_revenue,
        'total_units_sold': total_units_sold,
        'total_savings_delivered': total_savings_delivered,
    })
