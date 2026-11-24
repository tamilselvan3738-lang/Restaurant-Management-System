from django import forms
from .models import Payment, ConsolidatedPayment

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_method', 'amount', 'tendered_amount', 'transaction_reference']
        widgets = {
            'payment_method': forms.Select(attrs={'class': 'form-select', 'id': 'payment_method_select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'amount_input'}),
            'tendered_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'tendered_input', 'placeholder': 'Amount customer gave'}),
            'transaction_reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional transaction ID / ref'}),
        }


class ConsolidatedPaymentForm(forms.ModelForm):
    class Meta:
        model = ConsolidatedPayment
        fields = ['payment_method', 'amount', 'tendered_amount', 'transaction_reference', 'notes']
        widgets = {
            'payment_method': forms.Select(attrs={'class': 'form-select', 'id': 'consolidated_payment_method_select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'consolidated_amount_input'}),
            'tendered_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'consolidated_tendered_input', 'placeholder': 'Amount customer gave'}),
            'transaction_reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional transaction ID / ref'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional settlement notes'}),
        }


class DiscountApplyForm(forms.Form):
    discount_amount = forms.DecimalField(
        max_digits=8, decimal_places=2, min_value=0.00,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Discount $'})
    )
    discount_notes = forms.CharField(
        max_length=150, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Reason e.g. Staff 10%, Promo'})
    )
