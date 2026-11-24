from django import forms
from .models import MenuItem, Category, Ingredient, MenuItemIngredient

class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = [
            'category', 'name', 'description', 'price', 'cost_price',
            'preparation_time', 'calories', 'image', 'dietary_tags',
            'is_available', 'is_special'
        ]
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Item name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Short description of the dish'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'preparation_time': forms.NumberInput(attrs={'class': 'form-control'}),
            'calories': forms.NumberInput(attrs={'class': 'form-control'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'dietary_tags': forms.SelectMultiple(attrs={'class': 'form-select', 'size': '4'}),
            'is_available': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_special': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)
        if self.restaurant:
            self.fields['category'].queryset = Category.objects.filter(restaurant=self.restaurant, is_active=True)

    def save(self, commit=True):
        item = super().save(commit=False)
        if self.restaurant and not item.restaurant_id:
            item.restaurant = self.restaurant
        elif item.category and hasattr(item.category, 'restaurant') and item.category.restaurant:
            item.restaurant = item.category.restaurant
        if commit:
            item.save()
            self.save_m2m()
        return item


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description', 'icon', 'display_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Appetizers, Beverages'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'icon': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'bi-cup-hot'}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        cat = super().save(commit=False)
        if self.restaurant and not cat.restaurant_id:
            cat.restaurant = self.restaurant
        if commit:
            cat.save()
        return cat


class IngredientForm(forms.ModelForm):
    class Meta:
        model = Ingredient
        fields = ['name', 'unit', 'current_stock', 'low_stock_threshold', 'cost_per_unit']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'current_stock': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'low_stock_threshold': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'cost_per_unit': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        ing = super().save(commit=False)
        if self.restaurant and not ing.restaurant_id:
            ing.restaurant = self.restaurant
        if commit:
            ing.save()
        return ing


class StockUpdateForm(forms.Form):
    quantity_to_add = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Quantity to add'})
    )


from .models import FoodCombo, FoodComboItem

class FoodComboForm(forms.ModelForm):
    class Meta:
        model = FoodCombo
        fields = ['name', 'description', 'image', 'price', 'is_available', 'start_date', 'end_date']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Family Biryani Feast, South Indian Combo'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Brief description of the combo offer, servings, or dishes included'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '799.00', 'id': 'id_combo_price'}),
            'is_available': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_is_available'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, restaurant=None, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        combo = super().save(commit=False)
        if self.restaurant and not combo.restaurant_id:
            combo.restaurant = self.restaurant
        if commit:
            combo.save()
        return combo

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price <= 0:
            raise forms.ValidationError("Combo selling price must be greater than zero.")
        return price

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date cannot be earlier than start date.")
        return cleaned_data
