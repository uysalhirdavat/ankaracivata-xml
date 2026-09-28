import os
import sys
import time
import json
import html
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import requests


# ==================================================
# AYARLAR
# ==================================================

BASE_URL = "https://b2b.ankaracivata.com.tr"

LOGIN_URL = f"{BASE_URL}/api/Kullanici/login"
PRODUCTS_URL = f"{BASE_URL}/api/Malzeme/getAll"
DETAIL_URL = f"{BASE_URL}/api/Malzeme/getUrunDetay"

IMAGE_URL = f"{BASE_URL}/api/Urun/getUrunResim"
IMAGE_FILE_BASE_URL = f"{BASE_URL}/api/files"

OUTPUT_FILE = "ankaracivata.xml"
TMP_FILE = "ankaracivata.xml.tmp"

BARCODE_FILE = "ankaracivata_barcodes.json"
BARCODE_PREFIX = "uyl26092026999"
BARCODE_START = 1

DETAIL_FILE = "ankaracivata_details.json"
DETAIL_TMP_FILE = "ankaracivata_details.json.tmp"

IMAGE_FILE = "ankaracivata_images.json"
IMAGE_TMP_FILE = "ankaracivata_images.json.tmp"

DETAIL_GROUP_SIZE = 1000
DETAIL_DELAY = 0.20

IMAGE_GROUP_SIZE = 1000
IMAGE_DELAY = 0.20

GROUP_DELAY = 10

PAGE_SIZE = 1000
TIMEOUT = 60
MAX_RETRIES = 4

# Görsel sistemini doğrulamak için bildiğimiz ürün
IMAGE_TEST_PRODUCT_ID = "78491"


# ==================================================
# YARDIMCI
# ==================================================

def clean(value):
    if value is None:
        return ""

    return str(value).strip()


def html_clean(value):
    return html.escape(
        clean(value),
        quote=True
    )


def decimal_text(value, places=6):
    try:
        d = Decimal(str(value))

        return (
            f"{d:.{places}f}"
            .rstrip("0")
            .rstrip(".")
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):
        return "0"


def int_stock(value):
    try:
        d = Decimal(str(value))

        if d < 0:
            return 0
