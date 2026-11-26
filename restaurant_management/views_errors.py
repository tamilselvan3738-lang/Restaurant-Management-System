import logging
import uuid
from django.shortcuts import render
from django.http import JsonResponse, HttpResponseServerError

logger = logging.getLogger(__name__)


def is_api_or_ajax_request(request):
    """
    Detects if the incoming request is an AJAX request or expects a JSON response.
    """
    if not request:
        return False
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return True
    accept = request.headers.get('accept', '').lower()
    if 'application/json' in accept:
        return True
    if request.path.startswith('/api/') or request.path.startswith('/queue/api/'):
        return True
    return False


def custom_bad_request_view(request, exception=None):
    """Handler for HTTP 400 Bad Request."""
    if is_api_or_ajax_request(request):
        return JsonResponse({
            'success': False,
            'error': True,
            'status_code': 400,
            'message': 'Bad Request: The request was invalid or corrupted.',
            'detail': str(exception) if exception else None
        }, status=400)
    
    return render(request, '400.html', {'exception': exception}, status=400)


def custom_permission_denied_view(request, exception=None):
    """Handler for HTTP 403 Forbidden / Access Denied."""
    if is_api_or_ajax_request(request):
        return JsonResponse({
            'success': False,
            'error': True,
            'status_code': 403,
            'message': 'Access Denied: You do not have permission to access this restaurant resource.',
            'detail': str(exception) if exception else None
        }, status=403)

    return render(request, '403.html', {'exception': exception}, status=403)


def custom_page_not_found_view(request, exception=None):
    """Handler for HTTP 404 Page / Resource Not Found."""
    if is_api_or_ajax_request(request):
        return JsonResponse({
            'success': False,
            'error': True,
            'status_code': 404,
            'message': 'Resource Not Found: The requested endpoint, record, or dish does not exist.',
            'path': request.path if request else None
        }, status=404)

    return render(request, '404.html', {'path': request.path if request else ''}, status=404)


def custom_server_error_view(request):
    """Handler for HTTP 500 Internal Server Error."""
    error_id = uuid.uuid4().hex[:10].upper()
    logger.error(f"[500 ERROR REF-{error_id}] Internal server error on path {getattr(request, 'path', 'unknown')}", exc_info=True)

    if is_api_or_ajax_request(request):
        return JsonResponse({
            'success': False,
            'error': True,
            'status_code': 500,
            'message': 'An unexpected server error occurred. Please try again shortly.',
            'reference_id': error_id
        }, status=500)

    try:
        return render(request, '500.html', {'reference_id': error_id}, status=500)
    except Exception as e:
        logger.critical(f"Failed to render 500.html error template: {e}")
        # Bulletproof minimal standalone HTML fallback in case template engine fails
        fallback_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>500 - Server Error | Growwpark</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
        .card {{ background: #1e293b; padding: 2.5rem; border-radius: 1rem; max-width: 480px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h1 {{ color: #ef4444; font-size: 3rem; margin: 0 0 1rem; }}
        p {{ color: #94a3b8; line-height: 1.5; }}
        .code {{ font-family: monospace; background: #334155; padding: 0.25rem 0.5rem; border-radius: 0.25rem; color: #f59e0b; }}
        a {{ display: inline-block; margin-top: 1.5rem; padding: 0.75rem 1.5rem; background: #ea580c; color: #fff; text-decoration: none; border-radius: 0.5rem; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>500</h1>
        <h2>Server Error</h2>
        <p>Something went wrong on our end. Our technical team has been notified.</p>
        <p>Error Reference: <span class="code">REF-{error_id}</span></p>
        <a href="/">Return to Home</a>
    </div>
</body>
</html>"""
        return HttpResponseServerError(fallback_html, content_type='text/html')
