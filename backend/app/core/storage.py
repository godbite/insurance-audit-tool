import os
import logging
import boto3
import re
import unicodedata
from botocore.config import Config
from app.core.config import get_settings

log = logging.getLogger(__name__)

def sanitize_filename(filename: str) -> str:
    """Normalize and sanitize filename to prevent InvalidKey S3 errors."""
    # Normalize unicode to decompose accents and special characters (like narrow non-breaking spaces)
    normalized = unicodedata.normalize('NFKD', filename)
    # Convert to ASCII, ignoring characters that can't be converted
    ascii_only = normalized.encode('ascii', 'ignore').decode('ascii')
    # Replace non-alphanumeric (except dot, hyphen, underscore) with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9._-]', '_', ascii_only)
    # Collapse multiple consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized

def get_s3_client():
    """Create and return a boto3 S3 client using application settings."""
    settings = get_settings()
    endpoint = settings.minio_endpoint
    
    if not endpoint:
        log.warning("MinIO/S3 endpoint is not configured; S3 client cannot be initialized.")
        return None
        
    # Standardise endpoint to include schema if it doesn't already
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        if settings.minio_secure:
            endpoint = f"https://{endpoint}"
        else:
            endpoint = f"http://{endpoint}"

    try:
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name="ap-northeast-2",  # Default region matching pooler location
            config=Config(signature_version="s3v4"),
        )
    except Exception as e:
        log.warning(f"Failed to create S3 client: {e}")
        return None

def upload_file_to_s3(claim_id: str, file_id: str, filename: str, content: bytes) -> bool:
    """Upload file bytes to the S3 bucket under claims/{claim_id}/{file_id}/{filename}."""
    client = get_s3_client()
    if not client:
        log.warning("S3 client not available, skipping S3 upload.")
        return False
        
    settings = get_settings()
    safe_filename = sanitize_filename(filename)
    s3_key = f"claims/{claim_id}/{file_id}/{safe_filename}"
    
    try:
        client.put_object(
            Bucket=settings.minio_bucket,
            Key=s3_key,
            Body=content
        )
        log.info(f"Successfully uploaded {s3_key} to S3 bucket '{settings.minio_bucket}'.")
        return True
    except Exception as e:
        log.error(f"Failed to upload file {s3_key} to S3: {e}", exc_info=True)
        return False

def download_file_from_s3(claim_id: str, file_id: str, filename: str) -> bytes:
    """Download file bytes from the S3 bucket."""
    client = get_s3_client()
    if not client:
        log.warning("S3 client not available, skipping S3 download.")
        return b""
        
    settings = get_settings()
    safe_filename = sanitize_filename(filename)
    s3_key = f"claims/{claim_id}/{file_id}/{safe_filename}"
    
    try:
        response = client.get_object(
            Bucket=settings.minio_bucket,
            Key=s3_key
        )
        data = response["Body"].read()
        log.info(f"Successfully downloaded {s3_key} from S3 bucket '{settings.minio_bucket}'. Size: {len(data)} bytes.")
        return data
    except Exception as e:
        log.error(f"Failed to download file {s3_key} from S3: {e}", exc_info=True)
        return b""
