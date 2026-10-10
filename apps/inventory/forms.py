from decimal import Decimal

from django import forms
from django.utils import timezone

from config.jinja2 import money

from .models import Purchase, SupplierPayment
from .payments import AUTO, CREDIT, payable_purchases


class SupplierPaymentForm(forms.Form):
    date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(choices=SupplierPayment.Method.choices, initial=SupplierPayment.Method.BANK)
    reference = forms.CharField(
        max_length=100, required=False, label="Reference (optional)",
        widget=forms.TextInput(attrs={"placeholder": "Transaction ID / cheque no."}),
    )
    note = forms.CharField(max_length=255, required=False, label="Note (optional)")
    allocate_to = forms.ChoiceField(label="Apply to")

    def __init__(self, *args, supplier, **kwargs):
        super().__init__(*args, **kwargs)
        self.supplier = supplier
        self.bills = {str(p.pk): p for p in payable_purchases(supplier)}
        for p in self.bills.values():
            p.supplier = supplier  # due dates need the terms; avoid a query each
        self.fields["allocate_to"].choices = [
            (AUTO, "Oldest unpaid purchases first (recommended)"),
            *[(pk, f"{p.reference} — {money(p.balance_due)} due"
                   + (" (overdue)" if p.is_overdue else "" if p.status == Purchase.Status.RECEIVED else " (advance on open PO)"))
              for pk, p in self.bills.items()],
            (CREDIT, "Don't apply — keep as supplier credit"),
        ]

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value > timezone.localdate():
            raise forms.ValidationError("Payment date can't be in the future.")
        return value

    def allocation_target(self):
        choice = self.cleaned_data["allocate_to"]
        return self.bills.get(choice, choice)
