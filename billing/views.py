from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from accounts.models import User
from accounts.decorators import role_required
from accounts.tenant_utils import get_current_restaurant, get_tenant_object_or_404, filter_by_tenant, is_impersonating_restaurant
from orders.models import Order
from tables.models import DiningTable, CustomerSession
from .models import Invoice, Payment, ConsolidatedBill, ConsolidatedBillItem, ConsolidatedPayment, BillRequest
from .forms import PaymentForm, DiscountApplyForm, ConsolidatedPaymentForm

@login_required
def invoice_list(request):
    """
    Retired page redirected to the active Bill Requests command center.
    """
    return redirect('billing:bill_request_list')


@login_required
def generate_or_view_invoice(request, order_id):
    order = get_tenant_object_or_404(Order.objects.prefetch_related('items'), request, id=order_id)
    if order.session and order.session.invoices.exists():
        inv = order.session.invoices.first()
        inv.recalculate(save=True)
        return redirect('billing:invoice_detail', invoice_id=inv.id)

    existing = Invoice.objects.filter(order=order).first()
    if existing:
        existing.recalculate(save=True)
        return redirect('billing:invoice_detail', invoice_id=existing.id)

    restaurant = get_current_restaurant(request) or order.restaurant
    inv = Invoice.objects.create(
        restaurant=restaurant,
        order=order,
        session=order.session,
        table=order.table,
        invoice_type=Invoice.InvoiceType.SESSION_BILL if order.session else Invoice.InvoiceType.SINGLE_ORDER
    )
    inv.recalculate(save=True)
    messages.success(request, f"Invoice {inv.invoice_number} generated for {order.order_number}.")
    return redirect('billing:invoice_detail', invoice_id=inv.id)


@login_required
def invoice_detail(request, invoice_id):
    invoice = get_tenant_object_or_404(
        Invoice.objects.select_related('table', 'session', 'order').prefetch_related('payments__cashier'),
        request,
        id=invoice_id
    )

    # Recalculate invoice to ensure live order amounts are synced
    invoice.recalculate(save=True)

    payment_form = PaymentForm(initial={'amount': invoice.balance_due, 'tendered_amount': invoice.balance_due})
    discount_form = DiscountApplyForm(initial={'discount_amount': invoice.discount_amount, 'discount_notes': invoice.discount_notes})

    # Orders associated with this invoice (via session or single order)
    if invoice.session:
        orders = invoice.session.orders.exclude(status=Order.Status.CANCELLED).prefetch_related('items__menu_item')
    elif invoice.order:
        orders = [invoice.order]
    else:
        orders = []

    return render(request, 'billing/invoice_detail.html', {
        'invoice': invoice,
        'orders': orders,
        'payment_form': payment_form,
        'discount_form': discount_form,
        'paid_success': request.GET.get('paid_success') == '1',
    })


@login_required
@transaction.atomic
def process_payment(request, invoice_id):
    invoice = get_tenant_object_or_404(Invoice, request, id=invoice_id)
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.restaurant = invoice.restaurant
            payment.cashier = request.user

            # Handle cash tendered & change calculation
            if payment.payment_method == Payment.PaymentMethod.CASH and payment.tendered_amount > payment.amount:
                payment.change_returned = payment.tendered_amount - payment.amount
            else:
                payment.change_returned = Decimal('0.00')

            payment.status = Payment.Status.COMPLETED
            payment.save()

            # Recalculate invoice status
            invoice.recalculate(save=True)

            if invoice.status == Invoice.Status.PAID:
                # If tied to a session, close session
                if invoice.session:
                    invoice.session.close_session()
                elif invoice.order:
                    invoice.order.status = Order.Status.COMPLETED
                    invoice.order.save(update_fields=['status'])

                messages.success(request, f"Full payment of ₹{payment.amount} received! Change to return: ₹{payment.change_returned}.")
                return redirect(f"{reverse('billing:invoice_detail', args=[invoice.id])}?paid_success=1")
            else:
                messages.warning(request, f"Partial payment of ₹{payment.amount} recorded. Remaining balance: ₹{invoice.balance_due}.")
                return redirect('billing:invoice_detail', invoice_id=invoice.id)

    return redirect('billing:invoice_detail', invoice_id=invoice.id)


@login_required
def split_bill(request, invoice_id):
    invoice = get_tenant_object_or_404(Invoice, request, id=invoice_id)
    if request.method == 'POST':
        try:
            count = max(1, int(request.POST.get('split_count', 1)))
            invoice.split_count = count
            invoice.save(update_fields=['split_count'])
            messages.success(request, f"Bill split into {count} equal shares of ₹{invoice.split_amount_per_person} each.")
        except ValueError:
            pass
    return redirect('billing:invoice_detail', invoice_id=invoice.id)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def apply_discount(request, invoice_id):
    invoice = get_tenant_object_or_404(Invoice, request, id=invoice_id)
    if request.method == 'POST':
        form = DiscountApplyForm(request.POST)
        if form.is_valid():
            discount_amount = form.cleaned_data['discount_amount']
            discount_notes = form.cleaned_data['discount_notes']

            invoice.discount_amount = discount_amount
            invoice.discount_notes = discount_notes
            invoice.recalculate(save=True)

            messages.success(request, f"Discount of ₹{discount_amount} applied successfully.")

    return redirect('billing:invoice_detail', invoice_id=invoice.id)


@login_required
def print_receipt(request, invoice_id):
    invoice = get_tenant_object_or_404(
        Invoice.objects.select_related('table', 'session', 'order').prefetch_related('payments'),
        request,
        id=invoice_id
    )

    if invoice.session:
        orders = invoice.session.orders.exclude(status=Order.Status.CANCELLED).prefetch_related('items__menu_item')
    elif invoice.order:
        orders = [invoice.order]
    else:
        orders = []

    return render(request, 'billing/print_receipt.html', {
        'invoice': invoice,
        'orders': orders,
    })


# ==============================================================================
# MERGE CUSTOMER BILLS & CONSOLIDATED BILLING WORKFLOW
# ==============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def merge_bills_select(request):
    """
    Step 1 & 2: Displays eligible active and unpaid customer bills belonging to the user's restaurant.
    Allows Admin or Cashier to select two or more bills to combine into one consolidated bill.
    """
    # Base query: eligible unpaid or partially paid bills not yet merged into another consolidated bill
    invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.UNPAID, Invoice.Status.PARTIALLY_PAID],
        consolidated_bill__isnull=True
    ).select_related('table', 'session', 'order').prefetch_related(
        'payments',
        'session__orders__items__menu_item',
        'order__items__menu_item'
    )
    invoices = filter_by_tenant(invoices, request).order_by('-created_at')

    # Handle selection submission -> forward to preview
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_invoices')
        if len(selected_ids) < 2:
            messages.error(request, "Please select at least two customer bills to merge.")
            return redirect('billing:merge_bills_select')

        # Store in session and redirect to confirmation preview
        request.session['merge_invoice_ids'] = selected_ids
        return redirect('billing:merge_bills_preview')

    # Decorate invoices with ordered items summary for display
    invoice_data = []
    for inv in invoices:
        items_summary = []
        if inv.session:
            for ord_obj in inv.session.orders.exclude(status=Order.Status.CANCELLED):
                for item in ord_obj.items.exclude(status='CANCELLED'):
                    items_summary.append(f"{item.quantity}x {item.menu_item.name}")
        elif inv.order:
            for item in inv.order.items.exclude(status='CANCELLED'):
                items_summary.append(f"{item.quantity}x {item.menu_item.name}")

        invoice_data.append({
            'invoice': inv,
            'items_summary': items_summary,
            'items_text': ", ".join(items_summary) if items_summary else "No dishes recorded",
        })

    return render(request, 'billing/merge_bills_select.html', {
        'invoice_data': invoice_data,
        'total_eligible': len(invoice_data),
    })


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def merge_bills_preview(request):
    """
    Step 3: Merge Confirmation Preview displaying:
    - All selected original bills
    - All food items from each bill
    - Original table numbers & session references
    - Individual bill amounts & combined subtotal, taxes, discounts, service charges, final total
    - Two options: Confirm Merge or Cancel
    """
    selected_ids = request.session.get('merge_invoice_ids', [])
    if len(selected_ids) < 2:
        messages.error(request, "Please select at least two customer bills to merge.")
        return redirect('billing:merge_bills_select')

    # Fetch and validate all selected invoices with tenant filtering
    invoices = Invoice.objects.filter(
        id__in=selected_ids,
        status__in=[Invoice.Status.UNPAID, Invoice.Status.PARTIALLY_PAID],
        consolidated_bill__isnull=True
    ).select_related('table', 'session', 'order').prefetch_related(
        'payments',
        'session__orders__items__menu_item',
        'order__items__menu_item'
    )
    invoices = filter_by_tenant(invoices, request)

    if invoices.count() < 2:
        messages.error(request, "One or more selected bills are no longer eligible for merging. Please select again.")
        return redirect('billing:merge_bills_select')

    # Gather itemized dish breakdowns per customer bill
    bill_summaries = []
    combined_subtotal = Decimal('0.00')
    combined_tax = Decimal('0.00')
    combined_service = Decimal('0.00')
    combined_discount = Decimal('0.00')
    combined_total = Decimal('0.00')
    combined_paid = Decimal('0.00')
    combined_balance = Decimal('0.00')

    for inv in invoices:
        inv_items = []
        if inv.session:
            for ord_obj in inv.session.orders.exclude(status=Order.Status.CANCELLED):
                for item in ord_obj.items.exclude(status='CANCELLED'):
                    inv_items.append({
                        'name': item.menu_item.name,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'total_price': item.total_price,
                    })
        elif inv.order:
            for item in inv.order.items.exclude(status='CANCELLED'):
                inv_items.append({
                    'name': item.menu_item.name,
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'total_price': item.total_price,
                })

    combined_subtotal = sum((inv.subtotal for inv in invoices), Decimal('0.00'))
    combined_tax = sum((inv.tax_amount for inv in invoices), Decimal('0.00'))
    combined_service = sum((inv.service_charge_amount for inv in invoices), Decimal('0.00'))
    combined_discount = sum((inv.discount_amount for inv in invoices), Decimal('0.00'))
    combined_total = sum((inv.total_amount for inv in invoices), Decimal('0.00'))
    combined_paid = sum((inv.total_paid for inv in invoices), Decimal('0.00'))
    combined_balance = sum((inv.balance_due for inv in invoices), Decimal('0.00'))

    for inv in invoices:
        inv_items = []
        if inv.session:
            for ord_obj in inv.session.orders.exclude(status=Order.Status.CANCELLED):
                for item in ord_obj.items.exclude(status='CANCELLED'):
                    inv_items.append({
                        'name': item.menu_item.name,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'total_price': item.total_price,
                    })
        elif inv.order:
            for item in inv.order.items.exclude(status='CANCELLED'):
                inv_items.append({
                    'name': item.menu_item.name,
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'total_price': item.total_price,
                })

        bill_summaries.append({
            'invoice': inv,
            'table_number': inv.table.table_number if inv.table else "N/A",
            'session_name': inv.session.session_name if inv.session else (inv.customer_name or "Single Order"),
            'order_number': inv.order.order_number if inv.order else (inv.session.orders.first().order_number if inv.session and inv.session.orders.exists() else "N/A"),
            'items': inv_items,
            'subtotal': inv.subtotal,
            'tax': inv.tax_amount,
            'discount': inv.discount_amount,
            'service_charge': inv.service_charge_amount,
            'total': inv.total_amount,
            'paid': inv.total_paid,
            'balance': inv.balance_due,
        })

    context = {
        'bill_summaries': bill_summaries,
        'invoices': invoices,
        'combined_subtotal': combined_subtotal,
        'combined_tax': combined_tax,
        'combined_service': combined_service,
        'combined_discount': combined_discount,
        'combined_total': combined_total,
        'combined_paid': combined_paid,
        'combined_balance': combined_balance,
        'selected_ids': selected_ids,
    }
    return render(request, 'billing/merge_preview.html', context)


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def merge_bills_confirm(request):
    """
    Step 4: Executes atomic merge transaction.
    - Generates one unique ConsolidatedBill.
    - Attaches all original bills via ConsolidatedBillItem and sets invoice.status = MERGED.
    - Preserves all original customer sessions, table assignments, and orders untouched.
    """
    selected_ids = request.session.get('merge_invoice_ids', []) or request.POST.getlist('selected_invoices')
    if len(selected_ids) < 2:
        messages.error(request, "Please select at least two customer bills to merge.")
        return redirect('billing:merge_bills_select')

    restaurant = get_current_restaurant(request)

    with transaction.atomic():
        # Lock eligible invoices for update to prevent concurrent race conditions
        invoices_qs = Invoice.objects.select_for_update().filter(
            id__in=selected_ids,
            status__in=[Invoice.Status.UNPAID, Invoice.Status.PARTIALLY_PAID],
            consolidated_bill__isnull=True
        )
        invoices = list(filter_by_tenant(invoices_qs, request))

        if len(invoices) < 2:
            messages.error(request, "Merge failed: One or more bills were already modified, merged, or paid by another cashier.")
            return redirect('billing:merge_bills_select')

        # Create Consolidated Bill record
        consolidated_bill = ConsolidatedBill.objects.create(
            restaurant=restaurant,
            created_by=request.user,
            notes=request.POST.get('merge_notes', '').strip()
        )

        # Attach original bills and update status to MERGED
        for inv in invoices:
            ConsolidatedBillItem.objects.create(
                consolidated_bill=consolidated_bill,
                invoice=inv,
                original_amount=inv.total_amount,
                outstanding_amount=inv.balance_due,
                allocated_amount=Decimal('0.00')
            )
            inv.status = Invoice.Status.MERGED
            inv.consolidated_bill = consolidated_bill
            inv.merged_at = timezone.now()
            inv.merged_by = request.user
            inv.save(update_fields=['status', 'consolidated_bill', 'merged_at', 'merged_by'])

        # Recalculate consolidated bill financial amounts
        consolidated_bill.recalculate(save=True)

    # Clear session key
    request.session.pop('merge_invoice_ids', None)

    messages.success(
        request,
        f"Consolidated Bill {consolidated_bill.bill_number} generated successfully! Combined {len(invoices)} bills with total ₹{consolidated_bill.total_amount}."
    )
    return redirect('billing:consolidated_detail', bill_id=consolidated_bill.id)


@login_required
def consolidated_bill_list(request):
    """
    Displays all consolidated bills with filter options.
    """
    status_filter = request.GET.get('status')
    bills = filter_by_tenant(ConsolidatedBill.objects.prefetch_related('original_invoices__table', 'payments'), request).order_by('-created_at')

    if status_filter:
        bills = bills.filter(status=status_filter)

    return render(request, 'billing/consolidated_list.html', {
        'bills': bills,
        'status_filter': status_filter,
        'statuses': ConsolidatedBill.Status.choices,
    })


@login_required
def consolidated_detail(request, bill_id):
    """
    Consolidated bill detail page showing:
    - Bill metadata, total, balance due, payment status
    - List of attached original customer bills with tables and sessions
    - Complete consolidated dish breakdown
    - Single consolidated payment form
    - Option to void/unmerge if no payments have occurred
    """
    consolidated_bill = get_tenant_object_or_404(
        ConsolidatedBill.objects.prefetch_related(
            'original_invoices__table',
            'original_invoices__session',
            'original_invoices__order',
            'payments__cashier',
            'bill_items__invoice'
        ),
        request,
        id=bill_id
    )

    consolidated_bill.recalculate(save=True)

    payment_form = ConsolidatedPaymentForm(initial={
        'amount': consolidated_bill.balance_due,
        'tendered_amount': consolidated_bill.balance_due
    })

    # Gather all food items from all merged bills
    combined_dishes = []
    for inv in consolidated_bill.original_invoices.all():
        if inv.session:
            for ord_obj in inv.session.orders.exclude(status=Order.Status.CANCELLED):
                for item in ord_obj.items.exclude(status='CANCELLED'):
                    combined_dishes.append({
                        'table': inv.table.table_number if inv.table else "N/A",
                        'session': inv.session.session_name if inv.session else (inv.customer_name or "Guest"),
                        'order_number': ord_obj.order_number,
                        'item_name': item.menu_item.name,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'total_price': item.total_price,
                    })
        elif inv.order:
            for item in inv.order.items.exclude(status='CANCELLED'):
                combined_dishes.append({
                    'table': inv.table.table_number if inv.table else "N/A",
                    'session': inv.customer_name or "Single Order",
                    'order_number': inv.order.order_number,
                    'item_name': item.menu_item.name,
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'total_price': item.total_price,
                })

    can_void = (
        consolidated_bill.status != ConsolidatedBill.Status.VOIDED and
        consolidated_bill.payments.filter(status='COMPLETED').count() == 0 and
        request.user.role in [User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER]
    )

    return render(request, 'billing/consolidated_detail.html', {
        'bill': consolidated_bill,
        'combined_dishes': combined_dishes,
        'payment_form': payment_form,
        'can_void': can_void,
        'paid_success': request.GET.get('paid_success') == '1',
    })


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def consolidated_pay(request, bill_id):
    """
    Step 7: Collects one consolidated payment for the merged bill.
    - Updates ConsolidatedBill payment status.
    - Records ConsolidatedPayment transaction.
    - Transparently allocates payment amount to each original bill's outstanding balance.
    - Marks original bills as PAID with auditable payment history.
    """
    with transaction.atomic():
        consolidated_bill = get_tenant_object_or_404(ConsolidatedBill.objects.select_for_update(), request, id=bill_id)

        if consolidated_bill.status == ConsolidatedBill.Status.PAID:
            messages.info(request, "This consolidated bill is already fully paid.")
            return redirect('billing:consolidated_detail', bill_id=consolidated_bill.id)

        form = ConsolidatedPaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.consolidated_bill = consolidated_bill
            payment.cashier = request.user

            # Handle cash tendered & change calculation
            if payment.payment_method == Payment.PaymentMethod.CASH and payment.tendered_amount > payment.amount:
                payment.change_returned = payment.tendered_amount - payment.amount
            else:
                payment.change_returned = Decimal('0.00')

            payment.status = Payment.Status.COMPLETED
            payment.save()

            # Transparent, auditable allocation of payment to original bills
            remaining_to_allocate = payment.amount
            for link in consolidated_bill.bill_items.select_for_update().select_related('invoice'):
                orig_inv = link.invoice
                orig_due = orig_inv.balance_due
                if orig_due > Decimal('0.00') and remaining_to_allocate > Decimal('0.00'):
                    allocated = min(orig_due, remaining_to_allocate)
                    link.allocated_amount += allocated
                    link.save(update_fields=['allocated_amount'])

                    # Record payment directly on original bill
                    Payment.objects.create(
                        invoice=orig_inv,
                        restaurant=orig_inv.restaurant,
                        payment_method=payment.payment_method,
                        amount=allocated,
                        tendered_amount=allocated,
                        change_returned=Decimal('0.00'),
                        transaction_reference=payment.transaction_reference or f"CONS-{consolidated_bill.bill_number}",
                        status=Payment.Status.COMPLETED,
                        cashier=request.user,
                        notes=f"Settled via Consolidated Bill {consolidated_bill.bill_number}"
                    )
                    orig_inv.recalculate(save=True)

                    # Mark original invoice as PAID
                    if orig_inv.status == Invoice.Status.PAID:
                        if orig_inv.session:
                            orig_inv.session.close_session()
                        elif orig_inv.order:
                            orig_inv.order.status = Order.Status.COMPLETED
                            orig_inv.order.save(update_fields=['status'])

                    remaining_to_allocate -= allocated

            # Recalculate parent consolidated bill
            consolidated_bill.recalculate(save=True)

            messages.success(
                request,
                f"Full consolidated payment of ₹{payment.amount} recorded successfully! (Change: ₹{payment.change_returned})"
            )
            return redirect(f"{reverse('billing:consolidated_detail', args=[consolidated_bill.id])}?paid_success=1")

    return redirect('billing:consolidated_detail', bill_id=bill_id)


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def consolidated_void(request, bill_id):
    """
    Step 5: Voids / reverses a consolidated bill under controlled conditions.
    Allowed only if no conflicting completed payments have been recorded.
    Restores original customer bills to their previous independent status.
    """
    with transaction.atomic():
        consolidated_bill = get_tenant_object_or_404(ConsolidatedBill.objects.select_for_update(), request, id=bill_id)

        if consolidated_bill.payments.filter(status='COMPLETED').exists():
            messages.error(request, "Cannot unmerge or void a consolidated bill that already has completed payments.")
            return redirect('billing:consolidated_detail', bill_id=consolidated_bill.id)

        # Restore original invoices
        for inv in consolidated_bill.original_invoices.select_for_update():
            inv.consolidated_bill = None
            inv.merged_at = None
            inv.merged_by = None
            inv.status = Invoice.Status.PARTIALLY_PAID if inv.total_paid > Decimal('0.00') else Invoice.Status.UNPAID
            inv.save(update_fields=['consolidated_bill', 'merged_at', 'merged_by', 'status'])

        consolidated_bill.status = ConsolidatedBill.Status.VOIDED
        consolidated_bill.save(update_fields=['status'])

        messages.success(
            request,
            f"Consolidated Bill {consolidated_bill.bill_number} has been voided. All original customer bills have been restored to independent status."
        )

    return redirect('billing:bill_request_list')


@login_required
def consolidated_print(request, bill_id):
    """
    Step 4 & 7: Printable and downloadable consolidated invoice.
    Shows all original bills, combined itemized dishes, table numbers, and payment receipts.
    """
    consolidated_bill = get_tenant_object_or_404(
        ConsolidatedBill.objects.prefetch_related(
            'original_invoices__table',
            'original_invoices__session',
            'original_invoices__order',
            'payments__cashier',
            'bill_items__invoice'
        ),
        request,
        id=bill_id
    )

    combined_dishes = []
    for inv in consolidated_bill.original_invoices.all():
        if inv.session:
            for ord_obj in inv.session.orders.exclude(status=Order.Status.CANCELLED):
                for item in ord_obj.items.exclude(status='CANCELLED'):
                    combined_dishes.append({
                        'table': inv.table.table_number if inv.table else "N/A",
                        'session': inv.session.session_name if inv.session else (inv.customer_name or "Guest"),
                        'order_number': ord_obj.order_number,
                        'item_name': item.menu_item.name,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'total_price': item.total_price,
                    })
        elif inv.order:
            for item in inv.order.items.exclude(status='CANCELLED'):
                combined_dishes.append({
                    'table': inv.table.table_number if inv.table else "N/A",
                    'session': inv.customer_name or "Single Order",
                    'order_number': inv.order.order_number,
                    'item_name': item.menu_item.name,
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'total_price': item.total_price,
                })

    return render(request, 'billing/consolidated_print.html', {
        'bill': consolidated_bill,
        'combined_dishes': combined_dishes,
    })


# ==============================================================================
# CUSTOMER BILL REQUEST MANAGEMENT (ADMIN & CASHIER)
# ==============================================================================

@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def bill_request_list(request):
    """
    Step 3 & 4: Bill Requests Dashboard for Admin and Cashier.
    Displays all bill requests belonging to the user's restaurant.
    Includes status filter tabs, live search, and action controls.
    """
    status_filter = request.GET.get('status', '').upper()
    query = request.GET.get('q', '').strip()

    requests_qs = BillRequest.objects.select_related(
        'table', 'session', 'invoice', 'accepted_by', 'restaurant'
    ).prefetch_related(
        'session__orders__items__menu_item'
    )
    restaurant = get_current_restaurant(request)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and user.is_superuser and not is_impersonating_restaurant(request):
        rest_filter = request.GET.get('restaurant_id') or request.GET.get('restaurant')
        if rest_filter and rest_filter.isdigit():
            requests_qs = requests_qs.filter(Q(restaurant_id=int(rest_filter)) | Q(table__restaurant_id=int(rest_filter)))
    elif restaurant:
        requests_qs = requests_qs.filter(
            Q(restaurant=restaurant) | Q(table__restaurant=restaurant) | Q(session__restaurant=restaurant) | Q(restaurant__isnull=True)
        )
    else:
        requests_qs = filter_by_tenant(requests_qs, request)
    requests_qs = requests_qs.order_by('-requested_at')

    # Counts per status for tab counters
    total_count = requests_qs.count()
    pending_count = requests_qs.filter(status=BillRequest.Status.PENDING).count()
    accepted_count = requests_qs.filter(status=BillRequest.Status.ACCEPTED).count()
    bill_generated_count = requests_qs.filter(status=BillRequest.Status.BILL_GENERATED).count()
    payment_pending_count = requests_qs.filter(status=BillRequest.Status.PAYMENT_PENDING).count()
    completed_count = requests_qs.filter(status__in=[BillRequest.Status.PAID, BillRequest.Status.COMPLETED]).count()
    cancelled_count = requests_qs.filter(status=BillRequest.Status.CANCELLED).count()

    if status_filter == 'PENDING':
        requests_qs = requests_qs.filter(status=BillRequest.Status.PENDING)
    elif status_filter == 'ACCEPTED':
        requests_qs = requests_qs.filter(status=BillRequest.Status.ACCEPTED)
    elif status_filter == 'BILL_GENERATED':
        requests_qs = requests_qs.filter(status=BillRequest.Status.BILL_GENERATED)
    elif status_filter == 'PAYMENT_PENDING':
        requests_qs = requests_qs.filter(status=BillRequest.Status.PAYMENT_PENDING)
    elif status_filter == 'COMPLETED':
        requests_qs = requests_qs.filter(status__in=[BillRequest.Status.PAID, BillRequest.Status.COMPLETED])
    elif status_filter == 'CANCELLED':
        requests_qs = requests_qs.filter(status=BillRequest.Status.CANCELLED)

    if query:
        requests_qs = requests_qs.filter(
            Q(request_number__icontains=query) |
            Q(table__table_number__icontains=query) |
            Q(session__session_name__icontains=query) |
            Q(session__customer_name__icontains=query) |
            Q(session__join_code__icontains=query)
        )

    decorated_requests = []
    for req_obj in requests_qs:
        decorated_requests.append({
            'req': req_obj,
            'items': req_obj.items_summary(),
            'orders': req_obj.orders_list(),
        })

    context = {
        'decorated_requests': decorated_requests,
        'status_filter': status_filter or 'ALL',
        'query': query,
        'total_count': total_count,
        'pending_count': pending_count,
        'accepted_count': accepted_count,
        'bill_generated_count': bill_generated_count,
        'payment_pending_count': payment_pending_count,
        'completed_count': completed_count,
        'cancelled_count': cancelled_count,
    }
    return render(request, 'billing/bill_request_list.html', context)


@login_required
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def bill_request_detail(request, pk):
    """
    Step 6: Detailed view of a customer bill request.
    Displays customer info, table number, order breakdown, item cancellations,
    billing breakdown, and staff action buttons.
    """
    bill_req = get_tenant_object_or_404(
        BillRequest.objects.select_related('table', 'session', 'invoice', 'accepted_by', 'restaurant'),
        request,
        pk=pk
    )

    session = bill_req.session
    orders = session.orders.prefetch_related('items__menu_item').order_by('created_at') if session else []

    invoice = bill_req.invoice
    if not invoice and session:
        restaurant = get_current_restaurant(request) or bill_req.restaurant
        invoice, _ = Invoice.objects.get_or_create(
            session=session,
            defaults={'table': bill_req.table, 'invoice_type': Invoice.InvoiceType.SESSION_BILL, 'restaurant': restaurant}
        )
        if not invoice.restaurant:
            invoice.restaurant = restaurant
            invoice.save(update_fields=['restaurant'])
        invoice.recalculate(save=True)
        bill_req.invoice = invoice
        bill_req.save(update_fields=['invoice'])

    if invoice:
        invoice.recalculate(save=True)
        bill_req.total_amount = invoice.total_amount
        bill_req.paid_amount = invoice.total_paid
        bill_req.balance_due = invoice.balance_due
        bill_req.save(update_fields=['total_amount', 'paid_amount', 'balance_due'])

    context = {
        'bill_req': bill_req,
        'session': session,
        'orders': orders,
        'invoice': invoice,
    }
    return render(request, 'billing/bill_request_detail.html', context)


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def bill_request_accept(request, pk):
    """
    Step 7: Accept Bill Request.
    Uses select_for_update() inside a transaction to prevent race conditions.
    If another staff already accepted it, alerts:
    'This bill request has already been accepted by [Staff Name].'
    """
    with transaction.atomic():
        bill_req = get_tenant_object_or_404(BillRequest.objects.select_for_update(), request, pk=pk)

        if bill_req.status != BillRequest.Status.PENDING:
            staff_name = bill_req.accepted_by.get_full_name() or bill_req.accepted_by.username if bill_req.accepted_by else "another staff member"
            messages.warning(request, f"This bill request has already been accepted by {staff_name}.")
            return redirect('billing:bill_request_detail', pk=bill_req.pk)

        bill_req.status = BillRequest.Status.ACCEPTED
        bill_req.accepted_by = request.user
        bill_req.accepted_at = timezone.now()
        bill_req.save(update_fields=['status', 'accepted_by', 'accepted_at'])

    staff_display = request.user.get_full_name() or request.user.username
    messages.success(request, f"Bill request {bill_req.request_number} accepted successfully by {staff_display}! Customer has been notified.")
    return redirect('billing:bill_request_detail', pk=bill_req.pk)


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def bill_request_generate_bill(request, pk):
    """
    Step 8: Generate or finalize bill for this bill request.
    Links the invoice, updates the bill request status to BILL_GENERATED.
    """
    bill_req = get_tenant_object_or_404(BillRequest, request, pk=pk)
    restaurant = get_current_restaurant(request) or bill_req.restaurant

    invoice, _ = Invoice.objects.get_or_create(
        session=bill_req.session,
        defaults={'table': bill_req.table, 'invoice_type': Invoice.InvoiceType.SESSION_BILL, 'restaurant': restaurant}
    )
    if not invoice.restaurant:
        invoice.restaurant = restaurant
        invoice.save(update_fields=['restaurant'])
    invoice.recalculate(save=True)

    bill_req.invoice = invoice
    bill_req.total_amount = invoice.total_amount
    bill_req.paid_amount = invoice.total_paid
    bill_req.balance_due = invoice.balance_due
    bill_req.status = BillRequest.Status.BILL_GENERATED
    bill_req.save(update_fields=['invoice', 'total_amount', 'paid_amount', 'balance_due', 'status'])

    messages.success(request, f"Invoice {invoice.invoice_number} generated for {bill_req.session.session_name}!")
    return redirect('billing:invoice_detail', invoice_id=invoice.id)


@login_required
@require_POST
@role_required([User.Role.ADMIN, User.Role.MANAGER, User.Role.CASHIER])
def bill_request_cancel(request, pk):
    """
    Cancels a bill request with an optional cancellation reason.
    """
    bill_req = get_tenant_object_or_404(BillRequest, request, pk=pk)

    reason = request.POST.get('cancellation_reason', 'Cancelled by staff.')
    bill_req.status = BillRequest.Status.CANCELLED
    bill_req.cancellation_reason = reason
    bill_req.completed_at = timezone.now()
    bill_req.save(update_fields=['status', 'cancellation_reason', 'completed_at'])

    messages.info(request, f"Bill request {bill_req.request_number} marked as Cancelled.")
    return redirect('billing:bill_request_list')


@login_required
def bill_request_poll_api(request):
    """
    Step 10: Real-time JSON polling endpoint for Admin & Cashier navbar/dashboard.
    Returns the count of pending bill requests and active items with full details
    (table, customer/person name, total amount, balance due, time, action URLs).
    """
    restaurant = get_current_restaurant(request)
    user = getattr(request, 'user', None)

    qs = BillRequest.objects.filter(status=BillRequest.Status.PENDING).select_related('table', 'session', 'restaurant')

    if user and user.is_authenticated and user.is_superuser and not is_impersonating_restaurant(request):
        rest_filter = request.GET.get('restaurant_id') or request.GET.get('restaurant')
        if rest_filter and rest_filter.isdigit():
            qs = qs.filter(Q(restaurant_id=int(rest_filter)) | Q(table__restaurant_id=int(rest_filter)))
    elif restaurant:
        qs = qs.filter(
            Q(restaurant=restaurant) | Q(table__restaurant=restaurant) | Q(session__restaurant=restaurant) | Q(restaurant__isnull=True)
        )
    else:
        qs = filter_by_tenant(qs, request)

    pending_count = qs.count()
    latest = qs.order_by('-requested_at').first()

    latest_data = None
    table_summary = ""
    if latest:
        table_number = latest.table.table_number if latest.table else "N/A"
        if pending_count == 1:
            table_summary = f"Table {table_number}"
        else:
            table_summary = f"Table {table_number} (+{pending_count - 1})"

        customer_name = latest.session.customer_name if latest.session and latest.session.customer_name else ""
        session_name = latest.session.session_name if latest.session and latest.session.session_name else ""

        if customer_name and session_name and customer_name != session_name:
            person_display = f"{customer_name} ({session_name})"
        elif customer_name:
            person_display = customer_name
        elif session_name:
            person_display = session_name
        else:
            person_display = "Guest"

        total_fmt = f"{latest.total_amount:.2f}"
        balance_fmt = f"{latest.balance_due:.2f}"
        paid_fmt = f"{latest.paid_amount:.2f}"

        latest_data = {
            'id': latest.id,
            'request_number': latest.request_number,
            'table': table_number,
            'person': person_display,
            'customer_name': customer_name or "Guest",
            'session_name': session_name or "",
            'total_amount': total_fmt,
            'balance_due': balance_fmt,
            'paid_amount': paid_fmt,
            'amount': total_fmt,
            'time': latest.requested_at.strftime('%I:%M %p'),
            'detail_url': reverse('billing:bill_request_detail', args=[latest.id]),
            'accept_url': reverse('billing:bill_request_accept', args=[latest.id]),
        }

    return JsonResponse({
        'pending_count': pending_count,
        'table_summary': table_summary,
        'latest': latest_data,
    })
