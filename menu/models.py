from django.db import models
from django.utils.text import slugify

class Category(models.Model):
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='categories',
        help_text="Restaurant tenant this category belongs to"
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='bi-cup-hot', help_text="Bootstrap or FontAwesome icon class")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'Categories'
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'name'],
                name='unique_category_per_restaurant',
                condition=models.Q(restaurant__isnull=False),
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DietaryTag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    badge_color = models.CharField(max_length=20, default='success', help_text="Bootstrap badge color e.g. success, warning, danger, info")

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='menu_items',
        help_text="Restaurant tenant this menu item belongs to"
    )
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    cost_price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text="Cost to make this item")
    preparation_time = models.PositiveIntegerField(default=15, help_text="Estimated prep time in minutes")
    calories = models.PositiveIntegerField(null=True, blank=True, help_text="Calorie count")
    image = models.ImageField(upload_to='menu_items/', blank=True, null=True)
    dietary_tags = models.ManyToManyField(DietaryTag, blank=True)
    is_available = models.BooleanField(default=True, help_text="Whether currently available to order")
    is_special = models.BooleanField(default=False, help_text="Chef recommendation or daily special")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.restaurant and self.category and hasattr(self.category, 'restaurant') and self.category.restaurant:
            self.restaurant = self.category.restaurant
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - ${self.price}"

    @property
    def profit_margin(self):
        if self.cost_price and self.price:
            return round(((self.price - self.cost_price) / self.price) * 100, 1)
        return 0.0

    @property
    def image_url(self):
        default_svg = '/static/images/default_dish.svg'
        if self.image:
            try:
                return self.image.url
            except Exception:
                return default_svg
        return default_svg


class Ingredient(models.Model):
    UNIT_CHOICES = [
        ('kg', 'Kilograms (kg)'),
        ('g', 'Grams (g)'),
        ('l', 'Liters (L)'),
        ('ml', 'Milliliters (ml)'),
        ('pcs', 'Pieces (pcs)'),
        ('box', 'Boxes (box)'),
    ]

    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='ingredients',
        help_text="Restaurant tenant this inventory ingredient belongs to"
    )
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='kg')
    current_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    low_stock_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=5.00)
    cost_per_unit = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    last_restocked = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['restaurant', 'name'],
                name='unique_ingredient_per_restaurant',
                condition=models.Q(restaurant__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.current_stock} {self.unit})"

    @property
    def is_low_stock(self):
        return self.current_stock <= self.low_stock_threshold


class MenuItemIngredient(models.Model):
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='recipe_ingredients')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name='used_in_items')
    quantity_required = models.DecimalField(max_digits=8, decimal_places=2, help_text="Quantity required per dish order")

    def __str__(self):
        return f"{self.quantity_required} {self.ingredient.unit} of {self.ingredient.name} for {self.menu_item.name}"


class FoodCombo(models.Model):
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, null=True, blank=True, related_name='food_combos')
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='combos/', blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Single fixed selling price for the entire combo")
    is_available = models.BooleanField(default=True, help_text="Whether currently available to order")
    start_date = models.DateField(null=True, blank=True, help_text="Optional promotion start date")
    end_date = models.DateField(null=True, blank=True, help_text="Optional promotion end date")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - ₹{self.price}"

    @property
    def individual_total_price(self):
        total = sum(item.menu_item.price * item.quantity for item in self.items.all())
        return total

    @property
    def savings_amount(self):
        from decimal import Decimal
        diff = self.individual_total_price - self.price
        return max(Decimal('0.00'), diff)

    @property
    def discount_percentage(self):
        from decimal import Decimal
        ind_total = self.individual_total_price
        if ind_total > Decimal('0.00'):
            return round(((ind_total - self.price) / ind_total) * 100, 1)
        return 0.0

    @property
    def items_count(self):
        return self.items.count()

    @property
    def total_items_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def is_valid_combo(self):
        return self.items_count >= 3

    @property
    def component_dishes_with_images(self):
        """
        Returns list of component menu items for rendering the multi-dish collage banner.
        Always guarantees a clean image_url fallback to default dish SVG if an image is missing or invalid.
        """
        dishes = []
        default_svg = '/static/images/default_dish.svg'
        for item in self.items.all():
            if item.menu_item:
                url = default_svg
                if item.menu_item.image:
                    try:
                        url = item.menu_item.image.url
                    except Exception:
                        url = default_svg
                dishes.append({
                    'name': item.menu_item.name,
                    'image_url': url or default_svg,
                    'quantity': item.quantity,
                    'price': item.menu_item.price,
                })
        return dishes

    @property
    def image_url(self):
        default_svg = '/static/images/default_dish.svg'
        if self.image:
            try:
                return self.image.url
            except Exception:
                return default_svg
        return default_svg

    @property
    def collage_preview(self):
        """
        Returns structured data for rendering a multi-dish collage banner.
        """
        dishes = self.component_dishes_with_images
        total = len(dishes)
        return {
            'count': total,
            'has_images': total > 0,
            'first': dishes[0] if total > 0 else None,
            'second': dishes[1] if total > 1 else None,
            'third': dishes[2] if total > 2 else None,
            'fourth': dishes[3] if total > 3 else None,
            'extra_count': max(0, total - 4),
            'all': dishes,
        }


class FoodComboItem(models.Model):
    combo = models.ForeignKey(FoodCombo, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='combo_memberships')
    quantity = models.PositiveIntegerField(default=1)
    unit_price_snapshot = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text="Price snapshot of the dish when added to combo")

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} in {self.combo.name}"

    def save(self, *args, **kwargs):
        if not self.unit_price_snapshot and self.menu_item_id:
            self.unit_price_snapshot = self.menu_item.price
        super().save(*args, **kwargs)
