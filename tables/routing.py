from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/table/(?P<table_number>[\w-]+)/group/(?P<session_token>[\w-]+)/$', consumers.GroupCartConsumer.as_asgi()),
    re_path(r'^ws/service-request/session/(?P<session_token>[\w-]+)/$', consumers.ServiceRequestSessionConsumer.as_asgi()),
    re_path(r'^ws/service-request/staff/(?P<restaurant_id>[\w-]+)/$', consumers.ServiceRequestStaffConsumer.as_asgi()),
    re_path(r'^ws/queue/(?P<queue_token>[\w-]+)/$', consumers.QueueConsumer.as_asgi()),
]
