import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Restaurant
from tables.models import (
    DiningTable,
    CustomerSession,
    RestaurantSeat,
    SessionSeatAllocation,
    ServiceRequest,
    ServiceRequestAuditLog,
    ServiceSLAConfig,
    WaiterTableAssignment,
    TableWaitingQueue,
)
from tables.services import (
    create_service_request,
    update_service_request_status,
    get_assigned_waiter_for_table,
    check_unaccepted_service_requests,
    handle_waiter_shift_end_or_logout,
    calculate_live_waiting_time,
    assign_table_to_queue_entry,
    seat_queue_customer,
)


class SmartWaiterAssistanceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Bistro Central')

        self.admin = User.objects.create_user(
            username='admin_assist',
            email='admin@bistro.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )

        self.waiter1 = User.objects.create_user(
            username='waiter_john',
            email='john@bistro.com',
            password='Password123!',
            role=User.Role.WAITER,
            restaurant=self.restaurant,
            is_on_duty=True
        )

        self.waiter2 = User.objects.create_user(
            username='waiter_sarah',
            email='sarah@bistro.com',
            password='Password123!',
            role=User.Role.WAITER,
            restaurant=self.restaurant,
            is_on_duty=True
        )

        self.table1 = DiningTable.objects.create(
            table_number='T-01',
            capacity=4,
            status=DiningTable.Status.OCCUPIED,
            restaurant=self.restaurant,
            is_active=True
        )

        self.table2 = DiningTable.objects.create(
            table_number='T-02',
            capacity=4,
            status=DiningTable.Status.OCCUPIED,
            restaurant=self.restaurant,
            is_active=True
        )

        # Map Waiter 1 to Table 1
        WaiterTableAssignment.objects.create(
            waiter=self.waiter1,
            table=self.table1,
            restaurant=self.restaurant,
            section_name='Main Hall',
            is_active=True
        )

        # Active customer session for Table 1
        self.session1 = CustomerSession.objects.create(
            table=self.table1,
            session_name='Family Dinner',
            customer_name='Alice Smith',
            status=CustomerSession.Status.ACTIVE,
            session_token='test-session-token-t1-9988'
        )

    def test_customer_create_service_request_success(self):
        """Customer submits assistance request with valid session token and receives automated waiter assignment."""
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        response = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({
                'request_type': 'WATER',
                'custom_message': 'Two glasses of cold water please'
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('request', data)

        # Verify DB
        req = ServiceRequest.objects.get(id=data['request']['id'])
        self.assertEqual(req.table, self.table1)
        self.assertEqual(req.session, self.session1)
        self.assertEqual(req.request_type, ServiceRequest.RequestType.WATER)
        self.assertEqual(req.status, ServiceRequest.Status.PENDING)
        self.assertEqual(req.assigned_waiter, self.waiter1)
        self.assertEqual(req.custom_message, 'Two glasses of cold water please')

        # Audit log created
        audit = ServiceRequestAuditLog.objects.filter(service_request=req).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, 'REQUEST_CREATED')

    def test_customer_request_invalid_session_rejected(self):
        """Customer request without valid session token is rejected with 403."""
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        response = self.client.post(
            f"{url}?session=fake-invalid-token",
            data=json.dumps({'request_type': 'WATER'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['success'])

    def test_customer_request_rate_limiting(self):
        """Customer submitting duplicate request within 20s receives duplicate alert without DB duplication."""
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        # First request
        self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'TISSUE'}),
            content_type='application/json'
        )
        self.assertEqual(ServiceRequest.objects.filter(table=self.table1, request_type='TISSUE').count(), 1)

        # Immediate second request
        response2 = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'TISSUE'}),
            content_type='application/json'
        )
        self.assertEqual(response2.status_code, 200)
        self.assertTrue(response2.json().get('is_duplicate'))
        self.assertEqual(ServiceRequest.objects.filter(table=self.table1, request_type='TISSUE').count(), 1)

    def test_customer_request_validation(self):
        """Invalid service type or overly long message (>255 chars) is rejected with 400."""
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})

        # Invalid type
        res1 = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'INVALID_TYPE'}),
            content_type='application/json'
        )
        self.assertEqual(res1.status_code, 400)

        # Message too long
        res2 = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'WATER', 'custom_message': 'X' * 260}),
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 400)

    def test_workload_balancing_fallback(self):
        """When a table has no direct waiter assignment, routes to available waiter with least pending requests."""
        # Table 2 has no direct assignment. Waiter 1 has 1 pending request, Waiter 2 has 0.
        ServiceRequest.objects.create(
            table=self.table1,
            session=self.session1,
            restaurant=self.restaurant,
            request_type=ServiceRequest.RequestType.WATER,
            assigned_waiter=self.waiter1,
            status=ServiceRequest.Status.PENDING
        )

        chosen_waiter = get_assigned_waiter_for_table(self.table2, restaurant=self.restaurant)
        self.assertEqual(chosen_waiter, self.waiter2)

    def test_waiter_service_lifecycle(self):
        """Full lifecycle: PENDING -> ACCEPTED -> IN_PROGRESS -> COMPLETED."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        self.assertEqual(req.status, ServiceRequest.Status.PENDING)

        # 1. Waiter accepts
        self.client.force_login(self.waiter1)
        accept_url = reverse('tables:waiter_request_accept', kwargs={'request_id': req.id})
        res_accept = self.client.post(accept_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_accept.status_code, 200)

        req.refresh_from_db()
        self.assertEqual(req.status, ServiceRequest.Status.ACCEPTED)
        self.assertEqual(req.accepted_by, self.waiter1)
        self.assertIsNotNone(req.accepted_at)

        # 2. Waiter starts service
        start_url = reverse('tables:waiter_request_start', kwargs={'request_id': req.id})
        res_start = self.client.post(start_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_start.status_code, 200)

        req.refresh_from_db()
        self.assertEqual(req.status, ServiceRequest.Status.IN_PROGRESS)

        # 3. Waiter completes service
        complete_url = reverse('tables:waiter_request_complete', kwargs={'request_id': req.id})
        res_comp = self.client.post(complete_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_comp.status_code, 200)

        req.refresh_from_db()
        self.assertEqual(req.status, ServiceRequest.Status.COMPLETED)
        self.assertEqual(req.completed_by, self.waiter1)
        self.assertIsNotNone(req.completed_at)

    def test_customer_cancel_pending_request(self):
        """Customer can cancel a pending request, but cannot cancel an accepted/in-progress request."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.BILL
        )
        url = reverse('tables:customer_service_request_cancel', kwargs={'table_number': 'T-01', 'request_id': req.id})

        # Cancel while pending
        res = self.client.post(f"{url}?session={self.session1.session_token}")
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, ServiceRequest.Status.CANCELLED)

        # If already accepted, cancellation is denied
        req2, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.OTHER
        )
        update_service_request_status(req2.id, ServiceRequest.Status.ACCEPTED, user=self.waiter1)

        url2 = reverse('tables:customer_service_request_cancel', kwargs={'table_number': 'T-01', 'request_id': req2.id})
        res2 = self.client.post(f"{url2}?session={self.session1.session_token}")
        self.assertEqual(res2.status_code, 400)

    def test_admin_reassign_waiter(self):
        """Admin can reassign a pending request from Waiter 1 to Waiter 2 using either URL route."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.SAUCE
        )
        req.assigned_waiter = self.waiter1
        req.save()
        self.assertEqual(req.assigned_waiter, self.waiter1)

        self.client.force_login(self.admin)
        # Test primary route
        reassign_url = reverse('tables:admin_service_request_reassign', kwargs={'request_id': req.id})
        res = self.client.post(reassign_url, {'waiter_id': self.waiter2.id})
        self.assertEqual(res.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.assigned_waiter, self.waiter2)

        # Test alias route requested by modal (/tables/admin/service-requests/<id>/reassign/)
        alias_url = f"/tables/admin/service-requests/{req.id}/reassign/"
        res_alias = self.client.post(alias_url, {'waiter_id': self.waiter1.id})
        self.assertEqual(res_alias.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.assigned_waiter, self.waiter1)

    def test_customer_create_service_request_with_category_quantity_priority(self):
        """Customer creates request specifying category, quantity, unit, and priority."""
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        response = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({
                'request_type': 'COLD_WATER',
                'request_category': 'WATER',
                'quantity': 3,
                'unit': 'Glasses',
                'priority': 'URGENT',
                'custom_message': 'Urgent chilled water for guest'
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        req = ServiceRequest.objects.get(id=data['request']['id'])
        self.assertEqual(req.request_type, 'WATER_COLD')
        self.assertEqual(req.request_category, 'WATER')
        self.assertEqual(req.quantity, 3)
        self.assertEqual(req.unit, 'Glasses')
        self.assertEqual(req.priority, 'URGENT')
        self.assertGreater(req.estimated_duration_minutes, 0)

    def test_no_quantity_options_normalize_quantity_and_unit(self):
        """
        Verify that for assistance options where quantity is not applicable
        (Call Waiter, Request Bill, Remove Plates, Clean Table, Other Help, Paper Tissues, Ketchup & Sauces),
        the request automatically normalizes to quantity=1 and unit=''.
        """
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        no_qty_types = ['WAITER', 'BILL', 'REMOVE_PLATES', 'CLEANING', 'TISSUE', 'SAUCE', 'OTHER']

        for req_type in no_qty_types:
            res = self.client.post(
                url,
                data=json.dumps({
                    'request_type': req_type,
                    'quantity': 5,
                    'unit': 'Visit',
                    'session_token': self.session1.session_token
                }),
                content_type='application/json'
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            req_id = data['request']['id']
            req = ServiceRequest.objects.get(id=req_id)
            self.assertEqual(req.quantity, 1, f"Failed for {req_type}: quantity should be 1")
            self.assertEqual(req.unit, '', f"Failed for {req_type}: unit should be empty")

        # In contrast, items with quantity (e.g. WATER) retain their quantity and unit
        res_water = self.client.post(
            url,
            data=json.dumps({
                'request_type': 'WATER',
                'quantity': 4,
                'unit': 'Glasses',
                'session_token': self.session1.session_token
            }),
            content_type='application/json'
        )
        self.assertEqual(res_water.status_code, 200)
        water_req = ServiceRequest.objects.get(id=res_water.json()['request']['id'])
        self.assertEqual(water_req.quantity, 4)
        self.assertEqual(water_req.unit, 'Glasses')

    def test_waiter_unable_to_fulfil_service_request(self):
        """Waiter marks request as unable to fulfil with reason and alternative suggestion."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.SAUCE,
            request_category=ServiceRequest.RequestCategory.TISSUE_CONDIMENT
        )
        self.client.force_login(self.waiter1)
        unable_url = reverse('tables:waiter_request_unable', kwargs={'request_id': req.id})
        res = self.client.post(
            unable_url,
            {'reason': 'Chilli sauce temporarily out of stock', 'alternative_suggestion': 'We have hot garlic dip available'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, ServiceRequest.Status.UNABLE_TO_FULFIL)
        self.assertEqual(req.unable_to_fulfil_reason, 'Chilli sauce temporarily out of stock')
        self.assertEqual(req.alternative_suggestion, 'We have hot garlic dip available')

    def test_customer_repeat_service_request(self):
        """Customer repeats a completed service request, producing a new distinct request."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.WATER,
            quantity=2,
            unit='Glasses'
        )
        update_service_request_status(req.id, ServiceRequest.Status.ACCEPTED, user=self.waiter1)
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter1)

        repeat_url = reverse('tables:customer_service_request_repeat', kwargs={'table_number': 'T-01', 'request_id': req.id})
        res = self.client.post(f"{repeat_url}?session={self.session1.session_token}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        new_id = data['request']['id']
        self.assertNotEqual(new_id, req.id)

        new_req = ServiceRequest.objects.get(id=new_id)
        self.assertEqual(new_req.request_type, ServiceRequest.RequestType.WATER)
        self.assertEqual(new_req.quantity, 2)
        self.assertEqual(new_req.status, ServiceRequest.Status.PENDING)

    def test_customer_feedback_service_request(self):
        """Customer submits 1-5 star rating and feedback on a completed request."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CUTLERY
        )
        update_service_request_status(req.id, ServiceRequest.Status.ACCEPTED, user=self.waiter1)
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter1)

        feedback_url = reverse('tables:customer_service_request_feedback', kwargs={'table_number': 'T-01', 'request_id': req.id})
        res = self.client.post(
            f"{feedback_url}?session={self.session1.session_token}",
            data=json.dumps({'rating': 5, 'notes': 'Prompt and very polite!'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.rating, 5)
        self.assertEqual(req.feedback_notes, 'Prompt and very polite!')
        self.assertIsNotNone(req.feedback_at)

    def test_customer_report_not_fulfilled(self):
        """Customer reports request not received after completion, escalating to management."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.TISSUE
        )
        update_service_request_status(req.id, ServiceRequest.Status.ACCEPTED, user=self.waiter1)
        update_service_request_status(req.id, ServiceRequest.Status.COMPLETED, user=self.waiter1)

        report_url = reverse('tables:customer_service_request_report_not_fulfilled', kwargs={'table_number': 'T-01', 'request_id': req.id})
        res = self.client.post(
            f"{report_url}?session={self.session1.session_token}",
            data=json.dumps({'reason': 'Server marked finished but never came'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertTrue(req.reported_not_fulfilled)
        self.assertEqual(req.report_reason, 'Server marked finished but never came')
        self.assertIsNotNone(req.reported_at)

    def test_renotice_service_request_before_2_minutes_rejected(self):
        """Re-notice should be blocked if request has been waiting less than 2 minutes (120s)."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        renotice_url = reverse('tables:customer_service_request_renotice', kwargs={'table_number': 'T-01', 'request_id': req.id})
        res = self.client.post(
            f"{renotice_url}?session={self.session1.session_token}",
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data['success'])
        self.assertIn("Re-notice available after 2 minutes", data['message'])

    def test_renotice_service_request_after_2_minutes_success(self):
        """Re-notice succeeds after 2+ minutes, increments counter, resets is_seen, and notifies staff."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        # Mark request as seen and backdate requested_at to 150 seconds ago (2.5 mins)
        two_and_half_mins_ago = timezone.now() - timezone.timedelta(seconds=150)
        ServiceRequest.objects.filter(id=req.id).update(
            requested_at=two_and_half_mins_ago,
            is_seen=True
        )

        renotice_url = reverse('tables:customer_service_request_renotice', kwargs={'table_number': 'T-01', 'request_id': req.id})
        res = self.client.post(
            f"{renotice_url}?session={self.session1.session_token}",
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['re_notice_count'], 1)

        req.refresh_from_db()
        self.assertEqual(req.re_notice_count, 1)
        self.assertFalse(req.is_seen)  # Reset so staff notification counts/badges re-pop
        self.assertIsNotNone(req.last_renotice_at)

        # Audit log verification
        audit = ServiceRequestAuditLog.objects.filter(service_request=req, action='CUSTOMER_RENOTICED').first()
        self.assertIsNotNone(audit)
        self.assertIn("Customer sent reminder #1", audit.notes)

        # Test customer status polling includes timestamps and flags
        status_url = reverse('tables:customer_service_request_status', kwargs={'table_number': 'T-01'})
        status_res = self.client.get(f"{status_url}?session={self.session1.session_token}")
        status_data = status_res.json()
        found = next(r for r in status_data['requests'] if r['id'] == req.id)
        self.assertIn('requested_at_ts', found)
        self.assertIn('waiting_seconds', found)
        self.assertTrue(found['can_re_notice'])
        self.assertEqual(found['re_notice_count'], 1)

        # Test waiter poll API returns latest_renotice
        self.client.force_login(self.waiter1)
        poll_url = reverse('tables:waiter_requests_poll_api')
        poll_res = self.client.get(poll_url)
        poll_data = poll_res.json()
        self.assertIsNotNone(poll_data['latest_renotice'])
        self.assertEqual(poll_data['latest_renotice']['id'], req.id)
        self.assertEqual(poll_data['latest_renotice']['re_notice_count'], 1)

    def test_submitting_active_request_again_updates_same_card_instead_of_duplicate(self):
        """When customer submits same request type while already active, it updates the existing card instead of creating duplicate."""
        req1, created1 = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        self.assertTrue(created1)
        self.assertEqual(req1.re_notice_count, 0)

        # Backdate by 30 seconds so it's not a rapid double-tap
        ServiceRequest.objects.filter(id=req1.id).update(
            requested_at=timezone.now() - timezone.timedelta(seconds=30)
        )

        req2, created2 = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        self.assertFalse(created2)
        self.assertEqual(req1.id, req2.id)  # Same request record!
        req1.refresh_from_db()
        self.assertEqual(req1.re_notice_count, 1)  # Incremented on same card
        self.assertFalse(req1.is_seen)

    def test_renotice_ordering_comes_first(self):
        """Among multiple cards, a re-notified card must be sorted to come first."""
        # Create 5 requests
        r1, _ = create_service_request(table=self.table1, session=self.session1, request_type=ServiceRequest.RequestType.WATER)
        r2, _ = create_service_request(table=self.table1, session=self.session1, request_type=ServiceRequest.RequestType.PLATES)
        r3, _ = create_service_request(table=self.table1, session=self.session1, request_type=ServiceRequest.RequestType.TISSUE)
        r4, _ = create_service_request(table=self.table1, session=self.session1, request_type=ServiceRequest.RequestType.SAUCE)

        # Re-notice r2 (the 2nd request)
        from tables.views_assistance import _order_requests_with_renotice_first
        ServiceRequest.objects.filter(id=r2.id).update(
            re_notice_count=2,
            last_renotice_at=timezone.now()
        )

        ordered = list(_order_requests_with_renotice_first(ServiceRequest.objects.filter(id__in=[r1.id, r2.id, r3.id, r4.id])))
        self.assertEqual(ordered[0].id, r2.id)  # r2 MUST come first!

    def test_dismiss_renotice_api_clears_count(self):
        """Staff clicking on the top-right notification badge clears the re-notice count so it vanishes, and sends an apology popup message to the customer."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.CLEANING
        )
        ServiceRequest.objects.filter(id=req.id).update(re_notice_count=3, is_seen=False)

        self.client.force_login(self.waiter1)
        url = reverse('tables:service_request_dismiss_renotice_api', kwargs={'request_id': req.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['re_notice_count'], 0)
        self.assertIn("Sorry for the delay", data['staff_apology_message'])

        req.refresh_from_db()
        self.assertEqual(req.re_notice_count, 0)
        self.assertTrue(req.is_seen)
        self.assertIsNotNone(req.staff_apology_at)
        self.assertIn("Sorry for the delay", req.staff_apology_message)

        # Audit log verification
        audit = ServiceRequestAuditLog.objects.filter(service_request=req, action='RENOTICE_DISMISSED').first()
        self.assertIsNotNone(audit)

        # Customer polling verification: customer receives staff apology message
        status_url = reverse('tables:customer_service_request_status', kwargs={'table_number': self.table1.table_number})
        cust_res = self.client.get(f"{status_url}?token={self.session1.session_token}")
        self.assertEqual(cust_res.status_code, 200)
        cust_data = cust_res.json()
        matching_req = next(r for r in cust_data['requests'] if r['id'] == req.id)
        self.assertIn("Sorry for the delay", matching_req['staff_apology_message'])
        self.assertIsNotNone(matching_req['staff_apology_at_ts'])

    def test_waiter_availability_status_filtering(self):
        """When assigned waiter is BUSY or ON_BREAK, automatic dispatch falls back to available on-duty waiter."""
        self.waiter1.availability_status = User.AvailabilityStatus.BUSY
        self.waiter1.save()

        # Customer creates request at Table 1 (normally mapped to Waiter 1)
        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        response = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'WATER'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        req_id = response.json()['request']['id']
        req = ServiceRequest.objects.get(id=req_id)
        # Should be dispatched to Waiter 2 since Waiter 1 is BUSY
        self.assertEqual(req.assigned_waiter, self.waiter2)
        self.assertIsNotNone(req.assigned_at)

    def test_unassigned_queue_when_no_waiter_available(self):
        """When all waiters are off duty or unavailable, request remains in queue with assigned_waiter=None."""
        self.waiter1.is_on_duty = False
        self.waiter1.availability_status = User.AvailabilityStatus.OFF_DUTY
        self.waiter1.save()

        self.waiter2.is_on_duty = False
        self.waiter2.availability_status = User.AvailabilityStatus.OFF_DUTY
        self.waiter2.save()

        url = reverse('tables:customer_service_request_create', kwargs={'table_number': 'T-01'})
        response = self.client.post(
            f"{url}?session={self.session1.session_token}",
            data=json.dumps({'request_type': 'WATER'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        req_id = response.json()['request']['id']
        req = ServiceRequest.objects.get(id=req_id)
        self.assertIsNone(req.assigned_waiter)
        self.assertEqual(req.status, ServiceRequest.Status.PENDING)

        # Audit log verifies unassigned notice
        audit = ServiceRequestAuditLog.objects.filter(service_request=req, action='UNASSIGNED_PENDING').first()
        self.assertIsNotNone(audit)
        self.assertIn("No waiter is currently available", audit.notes)

    def test_acceptance_timeout_auto_reassignment(self):
        """When assigned waiter does not accept within timeout (60s), system automatically reassigns to next available waiter."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.WATER
        )
        self.assertEqual(req.assigned_waiter, self.waiter1)

        # Backdate assigned_at to 75 seconds ago (exceeding 60s timeout)
        seventy_five_secs_ago = timezone.now() - timezone.timedelta(seconds=75)
        ServiceRequest.objects.filter(id=req.id).update(assigned_at=seventy_five_secs_ago)

        reassigned = check_unaccepted_service_requests(restaurant=self.restaurant)
        self.assertEqual(len(reassigned), 1)

        req.refresh_from_db()
        self.assertEqual(req.assigned_waiter, self.waiter2)
        self.assertEqual(req.reassigned_from, self.waiter1)
        self.assertIn("Automatic acceptance timeout", req.reassignment_reason)

        # Audit log verification
        audit = ServiceRequestAuditLog.objects.filter(service_request=req, action='AUTO_TIMEOUT_REASSIGNMENT').first()
        self.assertIsNotNone(audit)

    def test_waiter_shift_end_handover(self):
        """When waiter ends shift or logs out, unresolved pending requests are safely handed over to another available waiter."""
        req, _ = create_service_request(
            table=self.table1,
            session=self.session1,
            request_type=ServiceRequest.RequestType.WATER
        )
        self.assertEqual(req.assigned_waiter, self.waiter1)

        # Waiter 1 shift end / handover
        transferred = handle_waiter_shift_end_or_logout(self.waiter1, restaurant=self.restaurant)
        self.assertEqual(len(transferred), 1)

        req.refresh_from_db()
        self.assertEqual(req.assigned_waiter, self.waiter2)
        self.assertEqual(req.reassigned_from, self.waiter1)

        # Waiter 1 is now marked OFF_DUTY
        self.waiter1.refresh_from_db()
        self.assertFalse(self.waiter1.is_on_duty)
        self.assertEqual(self.waiter1.availability_status, User.AvailabilityStatus.OFF_DUTY)

    def test_waiter_toggle_availability_endpoint(self):
        """Waiter can toggle availability via API endpoint."""
        self.client.force_login(self.waiter1)
        toggle_url = reverse('tables:waiter_toggle_availability')

        # Set to ON_BREAK
        res = self.client.post(toggle_url, {'status': 'ON_BREAK'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.status_code, 200)
        self.waiter1.refresh_from_db()
        self.assertEqual(self.waiter1.availability_status, User.AvailabilityStatus.ON_BREAK)

        # Set back to AVAILABLE
        res2 = self.client.post(toggle_url, {'status': 'AVAILABLE'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res2.status_code, 200)
        self.waiter1.refresh_from_db()
        self.assertEqual(self.waiter1.availability_status, User.AvailabilityStatus.AVAILABLE)

    def test_admin_sla_config_update_with_timeout(self):
        """Admin can configure acceptance timeout seconds per service request type."""
        self.client.force_login(self.admin)
        update_url = reverse('tables:admin_sla_config_update')

        res = self.client.post(update_url, {
            'request_type': 'WATER',
            'target_minutes': 3,
            'warning_minutes': 2,
            'escalation_minutes': 5,
            'acceptance_timeout_seconds': 45
        })
        self.assertEqual(res.status_code, 302)

        cfg = ServiceSLAConfig.objects.filter(restaurant=self.restaurant, request_type='WATER').first()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.acceptance_timeout_seconds, 45)


class LiveTableQueueManagementTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.restaurant = Restaurant.objects.create(name='Bistro Central')

        self.admin = User.objects.create_user(
            username='admin_queue',
            email='admin_q@bistro.com',
            password='Password123!',
            role=User.Role.ADMIN,
            restaurant=self.restaurant
        )

        self.table_a = DiningTable.objects.create(
            table_number='T-10',
            capacity=4,
            status=DiningTable.Status.AVAILABLE,
            restaurant=self.restaurant,
            is_active=True
        )

    def test_public_queue_view(self):
        """Public queue view returns 200 with table statistics and estimates."""
        url = reverse('tables:queue_public')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live Table Queue")
        self.assertIn('available_count', response.context)
        self.assertIn('estimates', response.context)

    def test_customer_join_queue_success(self):
        """Customer joins waiting list, token Q-001 is generated, and user is redirected to live tracking."""
        url = reverse('tables:queue_join')
        response = self.client.post(url, {
            'customer_name': 'Vikram Rao',
            'customer_phone': '9876543210',
            'guest_count': 3,
            'special_requirements': 'High chair needed'
        })
        self.assertEqual(response.status_code, 302)

        entry = TableWaitingQueue.objects.filter(customer_phone='9876543210').first()
        self.assertIsNotNone(entry)
        self.assertTrue(entry.queue_token.startswith('Q-'))
        self.assertEqual(entry.status, TableWaitingQueue.Status.WAITING)
        self.assertEqual(entry.guest_count, 3)

        # Redirects to track view
        track_url = reverse('tables:queue_track', kwargs={'queue_token': entry.queue_token})
        self.assertRedirects(response, track_url)

    def test_customer_join_queue_phone_validation(self):
        """Invalid phone numbers fail form validation."""
        url = reverse('tables:queue_join')
        res = self.client.post(url, {
            'customer_name': 'Invalid Guest',
            'customer_phone': '123',  # Too short
            'guest_count': 2
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(TableWaitingQueue.objects.filter(customer_name='Invalid Guest').exists())

    def test_customer_join_queue_duplicate_phone_prevention(self):
        """Same phone joining queue on same day redirects to existing active ticket."""
        url = reverse('tables:queue_join')
        # First entry
        self.client.post(url, {
            'customer_name': 'Pooja',
            'customer_phone': '9123456789',
            'guest_count': 2
        })
        count_first = TableWaitingQueue.objects.filter(customer_phone='9123456789').count()
        self.assertEqual(count_first, 1)

        # Second entry with same phone
        res2 = self.client.post(url, {
            'customer_name': 'Pooja',
            'customer_phone': '9123456789',
            'guest_count': 2
        })
        self.assertEqual(res2.status_code, 302)
        # Should not create duplicate
        self.assertEqual(TableWaitingQueue.objects.filter(customer_phone='9123456789').count(), 1)

    def test_queue_lifecycle_calling_assignment_and_seating(self):
        """Staff calls customer, assigns available table, and confirms seated with automatic session creation."""
        entry = TableWaitingQueue.objects.create(
            restaurant=self.restaurant,
            customer_name='Meera Patel',
            customer_phone='9988776655',
            guest_count=4,
            status=TableWaitingQueue.Status.WAITING
        )
        self.client.force_login(self.admin)

        # 1. Staff calls customer
        call_url = reverse('tables:admin_queue_call', kwargs={'queue_id': entry.id})
        res_call = self.client.post(call_url)
        self.assertEqual(res_call.status_code, 302)

        entry.refresh_from_db()
        self.assertEqual(entry.status, TableWaitingQueue.Status.CALLED)
        self.assertIsNotNone(entry.called_at)

        # 2. Staff assigns Table T-10
        assign_url = reverse('tables:admin_queue_assign', kwargs={'queue_id': entry.id})
        res_assign = self.client.post(assign_url, {'table_id': self.table_a.id})
        self.assertEqual(res_assign.status_code, 302)

        entry.refresh_from_db()
        self.assertEqual(entry.status, TableWaitingQueue.Status.TABLE_ASSIGNED)
        self.assertEqual(entry.assigned_table, self.table_a)

        # 3. Staff confirms seated
        seated_url = reverse('tables:admin_queue_seat', kwargs={'queue_id': entry.id})
        res_seated = self.client.post(seated_url)
        self.assertEqual(res_seated.status_code, 302)

        entry.refresh_from_db()
        self.assertEqual(entry.status, TableWaitingQueue.Status.SEATED)
        self.assertIsNotNone(entry.seated_at)
        self.assertIsNotNone(entry.seated_session)

        # Verify CustomerSession and Table state
        session = entry.seated_session
        self.assertEqual(session.table, self.table_a)
        self.assertEqual(session.customer_name, 'Meera Patel')
        self.assertEqual(session.status, CustomerSession.Status.ACTIVE)

        self.table_a.refresh_from_db()
        self.assertEqual(self.table_a.status, DiningTable.Status.OCCUPIED)

        # Verify RestaurantSeats were allocated
        allocations = SessionSeatAllocation.objects.filter(session=session)
        self.assertEqual(allocations.count(), 4)

    def test_customer_track_view_and_poll_api(self):
        """Live tracking view and API endpoint return current position and status."""
        entry = TableWaitingQueue.objects.create(
            restaurant=self.restaurant,
            customer_name='Anand Roy',
            customer_phone='9811223344',
            guest_count=2,
            status=TableWaitingQueue.Status.WAITING
        )

        track_url = reverse('tables:queue_track', kwargs={'queue_token': entry.queue_token})
        res_view = self.client.get(track_url)
        self.assertEqual(res_view.status_code, 200)
        self.assertContains(res_view, entry.queue_token)

        api_url = reverse('tables:queue_status_live', kwargs={'queue_token': entry.queue_token})
        res_api = self.client.get(api_url)
        self.assertEqual(res_api.status_code, 200)
        data = res_api.json()
        self.assertEqual(data['token'], entry.queue_token)
        self.assertEqual(data['status'], 'WAITING')
        self.assertEqual(data['position'], 1)

    def test_customer_queue_cancellation(self):
        """Customer can cancel their waiting queue entry."""
        entry = TableWaitingQueue.objects.create(
            restaurant=self.restaurant,
            customer_name='Rohan',
            customer_phone='9776655443',
            guest_count=2,
            status=TableWaitingQueue.Status.WAITING
        )
        cancel_url = reverse('tables:queue_cancel', kwargs={'queue_token': entry.queue_token})
        res = self.client.post(cancel_url)
        self.assertEqual(res.status_code, 302)

        entry.refresh_from_db()
        self.assertEqual(entry.status, TableWaitingQueue.Status.CANCELLED)
