import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Add backend directory to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.core.storage import get_s3_client

def main():
    settings = get_settings()
    log.info(f"Checking MinIO/S3 configuration:")
    log.info(f"  Endpoint: {settings.minio_endpoint}")
    log.info(f"  Access Key: {settings.minio_access_key[:4] + '...' if settings.minio_access_key else None}")
    log.info(f"  Bucket: {settings.minio_bucket}")
    log.info(f"  Secure: {settings.minio_secure}")

    client = get_s3_client()
    if not client:
        log.error("Failed to initialize S3 client (check your configurations).")
        sys.exit(1)

    try:
        log.info("Attempting to list objects in bucket to test connection...")
        response = client.list_objects_v2(
            Bucket=settings.minio_bucket,
            MaxKeys=5
        )
        log.info("Connection successful!")
        if "Contents" in response:
            log.info("Found objects:")
            for obj in response["Contents"]:
                log.info(f"  - {obj['Key']} ({obj['Size']} bytes)")
        else:
            log.info("Bucket is empty.")
    except Exception as e:
        log.error(f"S3 connection test failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
