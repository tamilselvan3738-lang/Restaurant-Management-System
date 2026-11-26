import os
import re
import uuid
from io import BytesIO
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from PIL import Image

# Allowed safe image extensions
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.svg'}

# Magic byte signatures for safe formats
MAGIC_SIGNATURES = {
    'jpeg': (b'\xff\xd8\xff',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'webp': (b'RIFF', b'WEBP'),
}

# Dangerous patterns that indicate executable code or malicious payloads
DISALLOWED_MAGIC_SIGNATURES = [
    b'MZ',            # Windows PE executable / DLL
    b'\x7fELF',       # Linux ELF binary
    b'#!',            # Shell script
    b'<?php',         # PHP script
    b'<%',            # ASP script
]

DANGEROUS_SVG_PATTERNS = [
    re.compile(rb'<\s*script', re.IGNORECASE),
    re.compile(rb'javascript\s*:', re.IGNORECASE),
    re.compile(rb'data\s*:\s*text\/html', re.IGNORECASE),
    re.compile(rb'on\w+\s*=', re.IGNORECASE), # onload, onerror, onclick, etc.
    re.compile(rb'<\s*foreignObject', re.IGNORECASE),
]


def sanitize_filename(filename, prefix=''):
    """
    Sanitizes a filename: strips directory paths, special characters, and non-ASCII,
    generating a unique, secure filename while preserving the valid extension.
    """
    base, ext = os.path.splitext(filename)
    ext = ext.lower().strip()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = '.jpg'

    clean_base = slugify(base)[:30] or 'upload'
    unique_id = uuid.uuid4().hex[:10]
    
    if prefix:
        clean_prefix = slugify(prefix)[:20]
        return f"{clean_prefix}_{clean_base}_{unique_id}{ext}"
    return f"{clean_base}_{unique_id}{ext}"


def validate_image_file(uploaded_file, max_size_mb=5, allowed_extensions=None):
    """
    Validates an uploaded image file:
    1. Size limit enforcement
    2. Extension whitelist
    3. Magic byte inspection to prevent disguised executables
    4. SVG content sanitization against stored XSS
    5. Pillow image verification for raster formats
    """
    if not uploaded_file:
        return uploaded_file

    allowed_exts = set(allowed_extensions) if allowed_extensions else ALLOWED_IMAGE_EXTENSIONS
    
    # 1. Enforce size limit
    max_bytes = max_size_mb * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise ValidationError(f"File size exceeds the {max_size_mb}MB limit. Uploaded file is {uploaded_file.size / (1024*1024):.1f}MB.")

    # 2. Enforce extension whitelist
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in allowed_exts:
        raise ValidationError(f"Unsupported file extension '{ext}'. Allowed formats: {', '.join(sorted(allowed_exts))}.")

    # Read the beginning of the file for magic byte inspection
    initial_pos = uploaded_file.tell() if hasattr(uploaded_file, 'tell') else 0
    header = uploaded_file.read(1024)
    uploaded_file.seek(initial_pos)

    # 3. Check for disallowed executable signatures
    for sig in DISALLOWED_MAGIC_SIGNATURES:
        if header.startswith(sig) or sig in header[:128]:
            raise ValidationError("Security violation: Uploaded file contains forbidden executable code or scripts.")

    # 4. Format-specific inspection
    if ext == '.svg':
        # SVG validation
        full_content = uploaded_file.read()
        uploaded_file.seek(initial_pos)
        
        # Must contain <svg tag
        if b'<svg' not in full_content.lower():
            raise ValidationError("Invalid SVG image: missing '<svg' root tag.")
            
        # Check for dangerous XSS payloads in SVG
        for pattern in DANGEROUS_SVG_PATTERNS:
            if pattern.search(full_content):
                raise ValidationError("Security alert: The SVG file contains unsafe embedded scripts or event handlers.")
    else:
        # Check magic bytes for standard raster formats
        is_valid_header = False
        if ext in ['.jpg', '.jpeg'] and header.startswith(MAGIC_SIGNATURES['jpeg'][0]):
            is_valid_header = True
        elif ext == '.png' and header.startswith(MAGIC_SIGNATURES['png'][0]):
            is_valid_header = True
        elif ext == '.webp' and header.startswith(MAGIC_SIGNATURES['webp'][0]) and MAGIC_SIGNATURES['webp'][1] in header[:16]:
            is_valid_header = True

        if not is_valid_header:
            raise ValidationError(f"Invalid file content. The file signature does not match the extension '{ext}'.")

        # 5. Verify image integrity with Pillow
        try:
            full_bytes = uploaded_file.read()
            uploaded_file.seek(initial_pos)
            image_stream = BytesIO(full_bytes)
            with Image.open(image_stream) as img:
                img.verify()
        except Exception as e:
            raise ValidationError(f"Corrupted or invalid image file: {str(e)}")

    return uploaded_file
