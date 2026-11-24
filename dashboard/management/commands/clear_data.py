from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.sessions.models import Session

from billing.models import (
    ConsolidatedPayment,
    ConsolidatedBillItem,
    ConsolidatedBill,
    BillRequest,
    Payment,
    Invoice,
)
from orders.models import (
    ExtraFoodOrderLog,
    OrderCancellationLog,
    OrderStatusHistory,
    OrderItem,
    Order,
)
from kitchen.models import KitchenNotification, ItemAvailabilityReport
from tables.models import (
    ServiceRequestAuditLog,
    ServiceRequestEscalation,
    ServiceRequest,
    SessionCartItem,
    SessionCart,
    SeatAuditLog,
    SessionSeatAllocation,
    GroupMember,
    Reservation,
    TableWaitingQueue,
    CustomerSession,
    DiningTable,
    RestaurantSeat,
)
from dashboard.models import CustomerReview
from accounts.models import PasswordResetOTP
from superadmin.models import AdminReport


class Command(BaseCommand):
    help = (
        "Safely removes all dummy and test operational data (orders, billing, customer sessions, "
        "service requests, notifications, carts, reviews, and logs) while preserving core configuration "
        "(users, staff accounts, restaurants, menu categories/items, physical dining tables, seats, SLA configs)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-reviews",
            action="store_true",
            help="Keep customer reviews instead of clearing them.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting dummy operational data cleanup..."))

        with transaction.atomic():
            # 1. Billing & Payments
            cp_count, _ = ConsolidatedPayment.objects.all().delete()
            cbi_count, _ = ConsolidatedBillItem.objects.all().delete()
            cb_count, _ = ConsolidatedBill.objects.all().delete()
            br_count, _ = BillRequest.objects.all().delete()
            pay_count, _ = Payment.objects.all().delete()
            inv_count, _ = Invoice.objects.all().delete()

            # 2. Orders & Logs
            ef_count, _ = ExtraFoodOrderLog.objects.all().delete()
            ocl_count, _ = OrderCancellationLog.objects.all().delete()
            osh_count, _ = OrderStatusHistory.objects.all().delete()
            oi_count, _ = OrderItem.objects.all().delete()
            ord_count, _ = Order.objects.all().delete()

            # 3. Kitchen notifications & reports
            kn_count, _ = KitchenNotification.objects.all().delete()
            iar_count, _ = ItemAvailabilityReport.objects.all().delete()

            # 4. Service Requests & Logs
            sral_count, _ = ServiceRequestAuditLog.objects.all().delete()
            sre_count, _ = ServiceRequestEscalation.objects.all().delete()
            sr_count, _ = ServiceRequest.objects.all().delete()

            # 5. Customer Sessions, Carts & Seat Allocations
            sci_count, _ = SessionCartItem.objects.all().delete()
            sc_count, _ = SessionCart.objects.all().delete()
            sal_count, _ = SeatAuditLog.objects.all().delete()
            ssa_count, _ = SessionSeatAllocation.objects.all().delete()
            gm_count, _ = GroupMember.objects.all().delete()
            res_count, _ = Reservation.objects.all().delete()
            twq_count, _ = TableWaitingQueue.objects.all().delete()
            cs_count, _ = CustomerSession.objects.all().delete()

            # 6. Reviews & Reports
            ar_count, _ = AdminReport.objects.all().delete()
            cr_count = 0
            if not options.get("keep_reviews"):
                cr_count, _ = CustomerReview.objects.all().delete()

            # 7. Expired / Dummy OTPs
            otp_count, _ = PasswordResetOTP.objects.all().delete()

            # 8. Reset physical dining tables and seats to AVAILABLE
            updated_tables = DiningTable.objects.all().update(status="AVAILABLE")
            updated_seats = RestaurantSeat.objects.all().update(status="AVAILABLE")

            # 9. Clean anonymous / customer Django sessions (leave staff/admin logged in)
            anon_session_count = 0
            for session in Session.objects.all():
                try:
                    data = session.get_decoded()
                    if "_auth_user_id" not in data:
                        session.delete()
                        anon_session_count += 1
                except Exception:
                    session.delete()
                    anon_session_count += 1

        self.stdout.write(self.style.SUCCESS("All dummy operational data cleaned successfully!"))
        self.stdout.write(f"- Orders deleted: {ord_count} (Items: {oi_count}, Logs: {ef_count + ocl_count + osh_count})")
        self.stdout.write(f"- Invoices & Payments deleted: {inv_count} invoices, {pay_count} payments, {cb_count} consolidated bills")
        self.stdout.write(f"- Customer Sessions deleted: {cs_count} (Carts: {sc_count}, Group members: {gm_count})")
        self.stdout.write(f"- Service Requests deleted: {sr_count} (Audit logs: {sral_count})")
        self.stdout.write(f"- Kitchen Notifications deleted: {kn_count}")
        self.stdout.write(f"- Customer Reviews deleted: {cr_count}")
        self.stdout.write(f"- Anonymous sessions cleared: {anon_session_count}")
        self.stdout.write(f"- Physical Tables reset to AVAILABLE: {updated_tables}")
        self.stdout.write(f"- Restaurant Seats reset to AVAILABLE: {updated_seats}")
        self.stdout.write(self.style.SUCCESS("Core menu items, categories, staff accounts, and restaurant settings remain intact."))
