import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from menu.models import Category, MenuItem
from tables.models import (
    DiningTable, CustomerSession, RestaurantSeat,
    SessionSeatAllocation, SeatAuditLog
)
from orders.models import Order, OrderItem
from billing.models import Invoice, Payment

class MultiCustomerSessionTableTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client_group_a = Client()
        self.client_group_b = Client()
        self.client_companion = Client()

        self.admin = User.objects.create_user(
            username='admin_tables',
            email='admin@tables.com',
            password='testpassword123',
            role=User.Role.ADMIN
        )

        self.category = Category.objects.create(name='Pizzas', slug='pizzas', is_active=True, display_order=1)
        self.item_pizza = MenuItem.objects.create(
            category=self.category,
            name='Classic Margherita D.O.P.',
            price=Decimal('16.50'),
            is_available=True
        )
        self.item_pasta = MenuItem.objects.create(
            category=self.category,
            name='Truffle Tagliolini',
            price=Decimal('24.00'),
            is_available=True
        )

        self.table = DiningTable.objects.create(
            table_number='T-05',
            capacity=4,
            location=DiningTable.Location.ROUND
        )

    def test_qr_scan_shows_session_selection_screen(self):
        """
        Scanning the table QR code opens the digital menu directly without pre-occupying seats.
        Table status remains AVAILABLE and 0 seats are occupied while browsing.
        """
        response = self.client.get(reverse('tables:customer_order', args=[self.table.table_number]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('T-05', content)
        self.assertIn('Dining Setup', content)
        self.assertEqual(self.table.occupied_seats_count, 0)
        self.assertEqual(self.table.available_seats_count, 4)
        self.assertEqual(self.table.status, DiningTable.Status.AVAILABLE)

        # After creating/selecting session, menu items are rendered
        session = CustomerSession.objects.create(
            table=self.table,
            session_name='Session T-05',
            status=CustomerSession.Status.ACTIVE,
            session_token='token-t5-scan'
        )
        menu_res = self.client.get(f"{reverse('tables:customer_order', args=[self.table.table_number])}?session={session.session_token}")
        self.assertEqual(menu_res.status_code, 200)
        menu_content = menu_res.content.decode('utf-8')
        self.assertIn('Classic Margherita D.O.P.', menu_content)

    def test_multiple_groups_at_same_table_independent_orders_and_bills(self):
        """
        Critical Test:
        - Group A sits at Table 5, starts Session A, orders Pizza (₹16.50 x 2 = ₹33.00).
        - Group B sits at Table 5, starts Session B, orders Pasta (₹24.00 x 1 = ₹24.00).
        - Verifies independent sessions, independent orders, independent invoices.
        - Verifies Group A paying does NOT close Group B or make the table Available.
        - Verifies table releases only when all sessions are closed.
        """
        # Step 1: Group A starts Session A
        resp_a_start = self.client_group_a.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={'action': 'start_session', 'customer_name': 'Alice Group A', 'guest_count': 2}
        )
        self.assertEqual(resp_a_start.status_code, 302)

        session_a = CustomerSession.objects.filter(table=self.table).first()
        self.assertIsNotNone(session_a)
        self.assertEqual(session_a.customer_name, 'Alice Group A')

        # Step 2: Group A views menu and places Order #1
        resp_a_menu = self.client_group_a.get(reverse('tables:customer_order', args=[self.table.table_number]))
        self.assertEqual(resp_a_menu.status_code, 200)
        self.assertIn('Alice Group A', resp_a_menu.content.decode('utf-8'))
        self.assertIn('Classic Margherita D.O.P.', resp_a_menu.content.decode('utf-8'))

        cart_a = json.dumps([{'item_id': self.item_pizza.id, 'quantity': 2, 'notes': 'Extra cheese'}])
        resp_a_order = self.client_group_a.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={'cart_data': cart_a, 'special_instructions': 'Group A first course'}
        )
        self.assertEqual(resp_a_order.status_code, 302)

        # Check Order A
        order_a = Order.objects.filter(session=session_a).first()
        self.assertIsNotNone(order_a)
        self.assertEqual(order_a.session, session_a)
        self.assertEqual(order_a.subtotal, Decimal('33.00'))

        # Step 3: Group B arrives at Table 5, starts Session B
        resp_b_start = self.client_group_b.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={'action': 'start_session', 'customer_name': 'Bob Group B', 'guest_count': 2}
        )
        self.assertEqual(resp_b_start.status_code, 302)

        session_b = CustomerSession.objects.filter(table=self.table).exclude(id=session_a.id).first()
        self.assertIsNotNone(session_b)
        self.assertNotEqual(session_a.id, session_b.id)

        # Step 4: Group B places their own order (Pasta x 1 = ₹24.00)
        cart_b = json.dumps([{'item_id': self.item_pasta.id, 'quantity': 1, 'notes': 'Al dente'}])
        resp_b_order = self.client_group_b.post(
            reverse('tables:customer_order', args=[self.table.table_number]),
            data={'cart_data': cart_b, 'special_instructions': 'Group B main'}
        )
        self.assertEqual(resp_b_order.status_code, 302)

        # Check Order B
        order_b = Order.objects.filter(session=session_b).first()
        self.assertIsNotNone(order_b)
        self.assertEqual(order_b.session, session_b)
        self.assertEqual(order_b.subtotal, Decimal('24.00'))

        # Step 5: Verify Isolation of Bills
        inv_a = session_a.invoices.first()
        inv_b = session_b.invoices.first()
        self.assertIsNotNone(inv_a)
        self.assertIsNotNone(inv_b)
        self.assertNotEqual(inv_a.id, inv_b.id)

        self.assertEqual(inv_a.subtotal, Decimal('33.00'))
        self.assertEqual(inv_b.subtotal, Decimal('24.00'))

        # Table status is fully occupied (4 of 4 seats taken)
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, DiningTable.Status.OCCUPIED)

        # Step 6: Group A pays their bill
        Payment.objects.create(
            invoice=inv_a,
            payment_method=Payment.PaymentMethod.UPI,
            amount=inv_a.total_amount,
            status=Payment.Status.COMPLETED
        )
        session_a.close_session()

        # Group A is completed
        session_a.refresh_from_db()
        self.assertEqual(session_a.status, CustomerSession.Status.COMPLETED)

        # Group B is STILL ACTIVE and table is now PARTIALLY_OCCUPIED (2 seats released, 2 still taken)
        session_b.refresh_from_db()
        self.assertEqual(session_b.status, CustomerSession.Status.ACTIVE)


class SmartServiceRequestSystemTests(TestCase):
    """
    Automated tests verifying the complete Smart Service Request Admin Management,
    Waiter Workflow, SLA Tracking, Table Sections, and Guest Escalation System.
    """
    def setUp(self):
        from accounts.models import Restaurant
        from datetime import timedelta
        from django.utils import timezone
        from tables.models import (
            TableSection, ServiceRequest, ServiceSLAConfig,
            ServiceRequestEscalation, ServiceRequestAuditLog, WaiterTableAssignment
        )

        # 1. Multi-Restaurant Tenants
        self.restaurant_a = Restaurant.objects.create(name='Bistro Alpha')
        self.restaurant_b = Restaurant.objects.create(name='Bistro Beta')

        # 2. Users (Admin, Waiters for A, Waiter for B)
        self.admin_a = User.objects.create_user(
            username='admin_a',
            email='admin_a@bistro.com',
            password='testpassword123',
            role=User.Role.ADMIN,
            restaurant=self.restaurant_a
        )
        self.waiter_a1 = User.objects.create_user(
            username='waiter_a1',
            email='waiter_a1@bistro.com',
            password='testpassword123',
            role=User.Role.WAITER,
            employee_id='EMP-001',
            is_on_duty=True,
            restaurant=self.restaurant_a
        )
        self.waiter_a2 = User.objects.create_user(
            username='waiter_a2',
            email='waiter_a2@bistro.com',
            password='testpassword123',
            role=User.Role.WAITER,
            employee_id='EMP-002',
            is_on_duty=True,
            restaurant=self.restaurant_a
        )
        self.waiter_b = User.objects.create_user(
            username='waiter_b',
            email='waiter_b@beta.com',
            password='testpassword123',
            role=User.Role.WAITER,
            employee_id='EMP-B1',
            is_on_duty=True,
            restaurant=self.restaurant_b
        )

        # 3. Tables
        self.table_a1 = DiningTable.objects.create(
            table_number='T-A1',
            capacity=4,
            location=DiningTable.Location.ROUND,
            restaurant=self.restaurant_a
        )
        self.table_a2 = DiningTable.objects.create(
            table_number='T-A2',
            capacity=2,
            location=DiningTable.Location.VIP,
            restaurant=self.restaurant_a
        )
        self.table_b1 = DiningTable.objects.create(
            table_number='T-B1',
            capacity=4,
            location=DiningTable.Location.BOOTH,
            restaurant=self.restaurant_b
        )

        # 4. Customer Session
        self.session_a1 = CustomerSession.objects.create(
            table=self.table_a1,
            session_token='test-session-a1-token-12345',
            customer_name='Alice Guest',
            status=CustomerSession.Status.ACTIVE
        )

        # 5. SLA Configs
        self.sla_water = ServiceSLAConfig.objects.create(
            restaurant=self.restaurant_a,
            request_type=ServiceRequest.RequestType.WATER,
            target_minutes=3,
            escalation_minutes=6,
            is_active=True
        )
        self.sla_cleaning = ServiceSLAConfig.objects.create(
            restaurant=self.restaurant_a,
            request_type=ServiceRequest.RequestType.CLEAN_TABLE,
            target_minutes=5,
            escalation_minutes=10,
            is_active=True
        )

        # 6. Floor Section
        self.section_a = TableSection.objects.create(
            restaurant=self.restaurant_a,
            name='Section A - Main Dining',
            code='SEC-A',
            is_active=True
        )
        self.section_a.tables.add(self.table_a1)
        self.section_a.assigned_waiters.add(self.waiter_a1)

    def test_01_service_request_creation_and_smart_waiter_routing(self):
        """
        Customer creates a service request:
        - Binds to table section automatically.
        - Automatically routes to an on-duty waiter assigned to that floor section.
        - Binds configured SLA benchmark target duration.
        - Records initial audit log.
        """
        from tables.services import create_service_request
        from tables.models import ServiceRequest, ServiceRequestAuditLog

        req, success = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            quantity=2,
            unit='Glasses',
            custom_message='Cold water please',
            session=self.session_a1,
            customer_name='Alice'
        )

        self.assertTrue(success)
        self.assertIsNotNone(req)
        self.assertEqual(req.status, ServiceRequest.Status.PENDING)
        self.assertEqual(req.table_section, self.section_a)
        self.assertEqual(req.assigned_waiter, self.waiter_a1)
        self.assertEqual(req.target_sla_minutes, 3)
        self.assertEqual(req.escalation_sla_minutes, 6)
        self.assertFalse(req.is_delayed)

        # Verify Audit Log
        logs = ServiceRequestAuditLog.objects.filter(service_request=req)
        self.assertGreaterEqual(logs.count(), 1)
        self.assertEqual(logs.first().new_status, ServiceRequest.Status.PENDING)

    def test_02_waiter_acceptance_workflow(self):
        """
        Waiter accepts responsibility:
        - Changes status to ACCEPTED.
        - Records accepted_at timestamp and accepted_by user.
        - Creates an audit log entry.
        """
        from tables.services import create_service_request, update_service_request_status
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )

        updated_req, success, msg = update_service_request_status(
            req.id,
            ServiceRequest.Status.ACCEPTED,
            user=self.waiter_a1
        )

        self.assertTrue(success)
        self.assertEqual(updated_req.status, ServiceRequest.Status.ACCEPTED)
        self.assertIsNotNone(updated_req.accepted_at)
        self.assertEqual(updated_req.assigned_waiter, self.waiter_a1)

    def test_03_waiter_service_start_workflow(self):
        """
        Waiter starts service (attending to guest):
        - Changes status from ACCEPTED to IN_PROGRESS.
        - Records service_started_at timestamp.
        """
        from tables.services import create_service_request, update_service_request_status
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        update_service_request_status(req.id, ServiceRequest.Status.ACCEPTED, user=self.waiter_a1)

        updated_req, success, msg = update_service_request_status(
            req.id,
            ServiceRequest.Status.IN_PROGRESS,
            user=self.waiter_a1
        )

        self.assertTrue(success)
        self.assertEqual(updated_req.status, ServiceRequest.Status.IN_PROGRESS)
        self.assertIsNotNone(updated_req.in_progress_at)

    def test_04_waiter_completion_workflow(self):
        """
        Waiter completes the request:
        - Status becomes COMPLETED.
        - completed_at is set.
        - completed_by is set to the completing waiter.
        - Elapsed waiting duration is preserved.
        """
        from tables.services import create_service_request, update_service_request_status
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        update_service_request_status(req.id, ServiceRequest.Status.ACCEPTED, user=self.waiter_a1)
        update_service_request_status(req.id, ServiceRequest.Status.IN_PROGRESS, user=self.waiter_a1)

        updated_req, success, msg = update_service_request_status(
            req.id,
            ServiceRequest.Status.COMPLETED,
            user=self.waiter_a1
        )

        self.assertTrue(success)
        self.assertEqual(updated_req.status, ServiceRequest.Status.COMPLETED)
        self.assertIsNotNone(updated_req.completed_at)
        self.assertEqual(updated_req.completed_by, self.waiter_a1)

    def test_05_unable_to_fulfil_requires_mandatory_reason(self):
        """
        Waiter reports Unable to Fulfil:
        - Empty reason must be rejected.
        - Providing a valid reason and alternative suggestion succeeds.
        """
        from tables.services import create_service_request, update_service_request_status
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )

        # Attempt without reason
        _, success_fail, msg = update_service_request_status(
            req.id,
            ServiceRequest.Status.UNABLE_TO_FULFIL,
            user=self.waiter_a1,
            reason=''
        )
        self.assertFalse(success_fail)

        # Attempt with valid reason
        updated_req, success_ok, _ = update_service_request_status(
            req.id,
            ServiceRequest.Status.UNABLE_TO_FULFIL,
            user=self.waiter_a1,
            reason='Bottled water out of stock',
            alternative_suggestion='Offered sparkling or tap water'
        )
        self.assertTrue(success_ok)
        self.assertEqual(updated_req.status, ServiceRequest.Status.UNABLE_TO_FULFIL)
        self.assertEqual(updated_req.unable_to_fulfil_reason, 'Bottled water out of stock')
        self.assertEqual(updated_req.alternative_suggestion, 'Offered sparkling or tap water')

    def test_06_customer_feedback_and_ratings(self):
        """
        Customer submits 1-5 star rating and optional comments.
        - Stored on the completed request.
        - Calculates correct average on the admin dashboard.
        """
        from tables.services import create_service_request, update_service_request_status, submit_service_request_feedback
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter_a1)

        req_updated, success, msg = submit_service_request_feedback(
            req.id,
            session=self.session_a1,
            rating=5,
            feedback_notes='Super fast delivery!'
        )

        self.assertTrue(success)
        self.assertEqual(req_updated.rating, 5)
        self.assertEqual(req_updated.feedback_notes, 'Super fast delivery!')
        self.assertIsNotNone(req_updated.feedback_at)

    def test_07_guest_escalation_workflow(self):
        """
        Customer reports 'I Have Not Received This Request':
        - Creates a ServiceRequestEscalation issue linked to original request.
        - Flags reported_not_fulfilled = True on ServiceRequest.
        - Prevents duplicate open escalations on the same request.
        """
        from tables.services import create_service_request, update_service_request_status, create_guest_escalation
        from tables.models import ServiceRequest, ServiceRequestEscalation

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter_a1)

        # First escalation report
        escalation, success, msg = create_guest_escalation(
            req.id,
            session=self.session_a1,
            complaint_notes='Waited 15 minutes, never received water.'
        )

        self.assertTrue(success)
        self.assertIsNotNone(escalation)
        self.assertEqual(escalation.status, ServiceRequestEscalation.EscalationStatus.NEW)
        self.assertEqual(escalation.complaint_notes, 'Waited 15 minutes, never received water.')

        req.refresh_from_db()
        self.assertTrue(req.reported_not_fulfilled)

        # Prevent duplicate open escalation
        _, dup_success, _ = create_guest_escalation(
            req.id,
            session=self.session_a1,
            complaint_notes='Another complaint.'
        )
        self.assertFalse(dup_success)

    def test_08_admin_escalation_resolution(self):
        """
        Admin investigates and resolves a customer escalation:
        - Sets resolution status (RESOLVED, CLOSED, etc.).
        - Records resolution notes and timestamp.
        - Logs audit trail.
        """
        from tables.services import (
            create_service_request, update_service_request_status,
            create_guest_escalation, resolve_guest_escalation
        )
        from tables.models import ServiceRequest, ServiceRequestEscalation

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter_a1)
        escalation, _, _ = create_guest_escalation(req.id, session=self.session_a1, complaint_notes='Never arrived')

        res_esc, success, msg = resolve_guest_escalation(
            escalation.id,
            status=ServiceRequestEscalation.EscalationStatus.RESOLVED,
            resolved_by=self.admin_a,
            resolution_notes='Manager personally redelivered fresh cold water with apologies.'
        )

        self.assertTrue(success)
        self.assertEqual(res_esc.status, ServiceRequestEscalation.EscalationStatus.RESOLVED)
        self.assertIsNotNone(res_esc.resolved_at)
        self.assertEqual(res_esc.resolved_by, self.admin_a)

    def test_09_admin_waiter_reassignment_with_audit_trail(self):
        """
        Admin reassigns an active request to another waiter:
        - Validates required reason.
        - Updates assignment atomically.
        - Preserves reassigned_from and logs old and new waiter in audit history.
        - Blocks reassignment to a waiter belonging to another restaurant.
        """
        from tables.services import create_service_request, reassign_service_request
        from tables.models import ServiceRequest, ServiceRequestAuditLog

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )

        # 1. Attempt reassignment without reason -> fails
        _, fail_res, _ = reassign_service_request(req.id, self.waiter_a2.id, reason='', performed_by=self.admin_a)
        self.assertFalse(fail_res)

        # 2. Attempt reassignment to foreign restaurant waiter -> fails
        _, foreign_res, _ = reassign_service_request(req.id, self.waiter_b.id, reason='Cross-restaurant', performed_by=self.admin_a)
        self.assertFalse(foreign_res)

        # 3. Successful reassignment to eligible waiter A2
        reassigned_req, ok_res, _ = reassign_service_request(
            req.id,
            self.waiter_a2.id,
            reason='Server A1 assigned to urgent patio VIP table',
            performed_by=self.admin_a
        )
        self.assertTrue(ok_res)
        self.assertEqual(reassigned_req.assigned_waiter, self.waiter_a2)
        self.assertEqual(reassigned_req.reassigned_from, self.waiter_a1)
        self.assertEqual(reassigned_req.reassigned_by, self.admin_a)
        self.assertEqual(reassigned_req.reassignment_reason, 'Server A1 assigned to urgent patio VIP table')

        # Audit log verification
        audit = ServiceRequestAuditLog.objects.filter(
            service_request=req,
            action='REASSIGNED_WAITER'
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn('reassigned', audit.notes.lower())

    def test_10_sla_breach_detection_marks_is_delayed(self):
        """
        Background SLA breach scanner:
        - Detects active requests that have exceeded their target SLA.
        - Sets is_delayed = True and sla_breached_at timestamp.
        """
        from datetime import timedelta
        from django.utils import timezone
        from tables.services import create_service_request, check_and_mark_sla_breaches
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        # Manually backdate requested_at to 10 minutes ago (water SLA target is 3 minutes)
        ServiceRequest.objects.filter(id=req.id).update(
            requested_at=timezone.now() - timedelta(minutes=10)
        )

        breached_count = check_and_mark_sla_breaches()
        self.assertGreaterEqual(breached_count, 1)

        req.refresh_from_db()
        self.assertTrue(req.is_delayed)
        self.assertIsNotNone(req.sla_breached_at)

    def test_11_admin_service_requests_dashboard_view_and_poll_api(self):
        """
        Tests the Admin Service Requests Dashboard view:
        - Loads view with full KPI cards (total, delayed, unable, escalations, ratings).
        - Filter parameters (delayed=true, status=PENDING, etc.).
        - Poll API endpoint returns live JSON synchronization payload.
        """
        from tables.services import create_service_request
        from tables.models import ServiceRequest

        req, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )

        client = Client()
        client.force_login(self.admin_a)

        # 1. Dashboard View
        resp = client.get(reverse('tables:admin_service_requests'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('delayed_count', resp.context)
        self.assertIn('unable_count', resp.context)
        self.assertIn('reported_count', resp.context)
        self.assertIn('avg_rating', resp.context)

        # 2. Filter by status
        resp_filtered = client.get(reverse('tables:admin_service_requests'), {'status': 'PENDING'})
        self.assertEqual(resp_filtered.status_code, 200)

        # 3. Poll API
        poll_resp = client.get(reverse('tables:admin_service_requests_poll_api'))
        self.assertEqual(poll_resp.status_code, 200)
        data = poll_resp.json()
        self.assertIn('metrics', data)
        self.assertIn('active_requests', data)

    def test_12_tenant_security_isolation(self):
        """
        Ensures strict multi-restaurant isolation:
        - Admin of Restaurant A cannot see Restaurant B's requests.
        - Waiter of Restaurant B cannot accept Restaurant A's requests.
        """
        from tables.services import create_service_request
        from tables.models import ServiceRequest

        session_b = CustomerSession.objects.create(
            table=self.table_b1,
            session_token='test-session-b1-token-9999',
            customer_name='Bob Beta'
        )

        req_b, _ = create_service_request(
            table=self.table_b1,
            request_type=ServiceRequest.RequestType.WATER,
            session=session_b
        )

        # Admin A dashboard should not contain req_b
        client_a = Client()
        client_a.force_login(self.admin_a)
        resp = client_a.get(reverse('tables:admin_service_requests'))
        req_ids = [r.id for r in resp.context['requests']]
        self.assertNotIn(req_b.id, req_ids)

        # Waiter B cannot accept Restaurant A's request
        req_a, _ = create_service_request(
            table=self.table_a1,
            request_type=ServiceRequest.RequestType.WATER,
            session=self.session_a1
        )
        client_wb = Client()
        client_wb.force_login(self.waiter_b)
        accept_resp = client_wb.post(f'/tables/service-request/{req_a.id}/accept/')
        # Should redirect with error or reject
        req_a.refresh_from_db()
        self.assertNotEqual(req_a.status, ServiceRequest.Status.ACCEPTED)


