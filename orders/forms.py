from django import forms
from .models import Order, OrderItem
from tables.models import DiningTable

class OrderCreateForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['order_type', 'table', 'customer_name', 'customer_phone', 'special_instructions']
        widgets = {
            'order_type': forms.Select(attrs={'class': 'form-select', 'id': 'order_type_select'}),
            'table': forms.Select(attrs={'class': 'form-select', 'id': 'table_select'}),
            'customer_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Customer or Guest name'}),
            'customer_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone number (optional)'}),
            'special_instructions': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Special prep instructions, allergy alerts...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Show only active tables
        self.fields['table'].queryset = DiningTable.objects.filter(is_active=True)


class OrderItemAddForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ['menu_item', 'quantity', 'special_notes']
        widgets = {
            'menu_item': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'value': 1}),
            'special_notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Extra sauce, no onions'}),
        }
