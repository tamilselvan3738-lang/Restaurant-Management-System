import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def broadcast_group_cart_update(session_token, event_type='cart_updated', data=None):
    """
    Broadcasts real-time events to all active members of a group session.
    Supported events:
      - cart_updated: An item was added, updated, or removed
      - member_updated: A member joined, left, or had role updated
      - order_submitted: Order sent to kitchen
      - status_updated: Kitchen / bill status changed
    """
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"group_cart_{session_token}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'cart_message',
                    'event_type': event_type,
                    'data': data or {},
                }
            )
    except Exception as exc:
        logger.warning(f"Error broadcasting group cart update for session {session_token}: {exc}")


class GroupCartConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.table_number = self.scope['url_route']['kwargs'].get('table_number')
        self.session_token = self.scope['url_route']['kwargs'].get('session_token')
        self.group_name = f"group_cart_{self.session_token}"

        # Join the session's WebSocket group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave the session's WebSocket group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        try:
            payload = json.loads(text_data)
        except Exception:
            payload = {}

        msg_type = payload.get('type')
        if msg_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))

    async def cart_message(self, event):
        await self.send(text_data=json.dumps({
            'event_type': event.get('event_type', 'cart_updated'),
            'data': event.get('data', {})
        }))


class ServiceRequestSessionConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for active customer table sessions.
    Receives real-time updates when a waiter accepts, starts, or completes their service request.
    """
    async def connect(self):
        self.session_token = self.scope['url_route']['kwargs'].get('session_token')
        self.group_name = f"service_req_session_{self.session_token}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def service_message(self, event):
        await self.send(text_data=json.dumps({
            'event_type': event.get('event_type', 'request_updated'),
            'data': event.get('data', {})
        }))


class ServiceRequestStaffConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for Waiter and Admin dashboards.
    Receives real-time updates whenever a new service request is submitted or claimed.
    """
    async def connect(self):
        self.restaurant_id = self.scope['url_route']['kwargs'].get('restaurant_id', 'all')
        self.group_name = f"service_req_staff_{self.restaurant_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def service_message(self, event):
        await self.send(text_data=json.dumps({
            'event_type': event.get('event_type', 'staff_request_updated'),
            'data': event.get('data', {})
        }))


class QueueConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for waiting customers tracking their digital queue token live.
    Receives real-time position updates, table-ready alerts, and seating notifications.
    """
    async def connect(self):
        self.queue_token = self.scope['url_route']['kwargs'].get('queue_token')
        self.group_name = f"queue_token_{self.queue_token}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def queue_message(self, event):
        await self.send(text_data=json.dumps({
            'event_type': event.get('event_type', 'queue_updated'),
            'data': event.get('data', {})
        }))
