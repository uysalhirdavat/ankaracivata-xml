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

# Ürün görsellerini veren API
IMAGE_URL = f"{BASE_URL}/api/Urun/getUrunResim"

# Görsel dosyalarının gerçek erişim adresi
IMAGE_FILE_BASE_URL = f"{BASE_URL}/api/files"


OUTPUT_FILE = "ankaracivata.xml"
TMP_FILE = "ankaracivata.xml.tmp"


# ==================================================
# BARKOD
# ==================================================

BARCODE_FILE = "ankaracivata_barcodes.json"

BARCODE_PREFIX = "uyl26092026999"
BARCODE_START = 1


# ==================================================
# DETAY CACHE
# ==================================================

DETAIL_FILE = "ankaracivata_details.json"
DETAIL_TMP_FILE = "ankaracivata_details.json.tmp"


# ==================================================
# GÖRSEL CACHE
# ==================================================

IMAGE_FILE = "ankaracivata_images.json"
IMAGE_TMP_FILE = "ankaracivata_images.json.tmp"


# ==================================================
# TARAMA AYARLARI
# ==================================================

DETAIL_GROUP_SIZE = 1000
DETAIL_DELAY = 0.20

IMAGE_GROUP_SIZE = 1000
IMAGE_DELAY = 0.20

GROUP_DELAY = 10

PAGE_SIZE = 1000

TIMEOUT = 60
MAX_RETRIES = 4


# ==================================================
# YARDIMCI FONKSİYONLAR
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

        return int(d)

    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):
        return 0


# ==================================================
# HTTP / RETRY
# ==================================================

def request_with_retry(
    session,
    method,
    url,
    **kwargs
):
    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):
        try:
            response = session.request(
                method,
                url,
                timeout=TIMEOUT,
                **kwargs
            )

            if response.status_code == 200:
                return response

            last_error = RuntimeError(
                f"HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        except requests.RequestException as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            wait = attempt * 3

            print(
                f"İstek başarısız. "
                f"{wait} sn sonra "
                f"tekrar deneniyor..."
            )

            time.sleep(wait)

    raise RuntimeError(
        f"İstek başarısız: "
        f"{last_error}"
    )


# ==================================================
# LOGIN
# ==================================================

def login(session):
    username = os.environ.get(
        "ANKARA_KULLANICI_ADI"
    )

    password = os.environ.get(
        "ANKARA_SIFRE"
    )

    if not username or not password:
        raise RuntimeError(
            "ANKARA_KULLANICI_ADI veya "
            "ANKARA_SIFRE GitHub Secret "
            "bulunamadı."
        )

    payload = {
        "kullaniciAdi": username,
        "sifre": password
    }

    response = request_with_retry(
        session,
        "POST",
        LOGIN_URL,
        json=payload
    )

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Giriş başarısız: "
            f"{data.get('message')}"
        )

    token_data = (
        data
        .get("data", {})
        .get("token", {})
    )

    token = token_data.get("token")
    cari_id = token_data.get("cariId")

    if not token or cari_id is None:
        raise RuntimeError(
            "Login cevabından "
            "token/cariId alınamadı."
        )

    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": (
            "application/json, "
            "text/plain, */*"
        ),
        "Content-Type": "application/json"
    })

    print(
        "Ankara Civata girişi başarılı."
    )

    print(
        "Cari ID alındı. "
        "Token loglanmayacak."
    )

    return cari_id


# ==================================================
# TÜM ÜRÜNLER
# ==================================================

def fetch_page(
    session,
    cari_id,
    skip
):
    payload = {
        "cariId": cari_id,
        "cariIdList": str(cari_id),
        "parameters": {
            "sort": None,
            "filter": None,
            "group": None,
            "requireTotalCount": True,
            "searchOperation": "contains",
            "searchValue": None,
            "skip": skip,
            "take": PAGE_SIZE,
            "userData": {}
        }
    }

    response = request_with_retry(
        session,
        "POST",
        PRODUCTS_URL,
        json=payload
    )

    result = response.json()

    if not result.get("success"):
        raise RuntimeError(
            f"Ürün servisi başarısız: "
            f"{result.get('message')}"
        )

    load_result = (
        result
        .get("data", {})
        .get("loadResult", {})
    )

    products = load_result.get(
        "data",
        []
    )

    total_count = load_result.get(
        "totalCount"
    )

    if total_count is None:
        raise RuntimeError(
            "API totalCount döndürmedi."
        )

    return (
        products,
        int(total_count)
    )


def fetch_all_products(
    session,
    cari_id
):
    all_products = []

    seen_ids = set()

    skip = 0
    expected_total = None

    while True:
        products, total_count = (
            fetch_page(
                session,
                cari_id,
                skip
            )
        )

        if expected_total is None:
            expected_total = total_count

            print(
                f"API toplam ürün: "
                f"{expected_total}"
            )

        elif total_count != expected_total:
            raise RuntimeError(
                "Toplam ürün sayısı işlem "
                "sırasında değişti: "
                f"{expected_total} -> "
                f"{total_count}"
            )

        if not products:
            break

        for product in products:
            product_id = (
                product.get("malzemeId")
                or product.get("id")
            )

            if product_id is None:
                raise RuntimeError(
                    "Ürün ID bulunamadı."
                )

            product_id = str(
                product_id
            )

            if product_id in seen_ids:
                continue

            seen_ids.add(
                product_id
            )

            all_products.append(
                product
            )

        print(
            f"Çekilen: "
            f"{len(all_products)} / "
            f"{expected_total} "
            f"(skip={skip}, "
            f"gelen={len(products)})"
        )

        skip += len(products)

        if (
            len(all_products)
            >= expected_total
        ):
            break

        if len(products) < PAGE_SIZE:
            break

        time.sleep(0.4)

    if expected_total is None:
        raise RuntimeError(
            "Toplam ürün sayısı "
            "alınamadı."
        )

    if (
        len(all_products)
        != expected_total
    ):
        raise RuntimeError(
            f"EKSİK VERİ! "
            f"API toplam="
            f"{expected_total}, "
            f"benzersiz çekilen="
            f"{len(all_products)}. "
            f"XML güncellenmeyecek."
        )

    return all_products


# ==================================================
# KALICI UYL BARKOD
# ==================================================

def load_barcode_map():
    if not os.path.exists(
        BARCODE_FILE
    ):
        print(
            "Barkod eşleme dosyası "
            "henüz yok."
        )

        return {}

    try:
        with open(
            BARCODE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):
            raise RuntimeError(
                "Barkod dosyasının "
                "formatı geçersiz."
            )

        result = {}

        for (
            product_id,
            barcode
        ) in data.items():
            product_id = clean(
                product_id
            )

            barcode = clean(
                barcode
            )

            if product_id and barcode:
                result[
                    product_id
                ] = barcode

        print(
            f"Mevcut barkod eşlemesi: "
            f"{len(result)} ürün"
        )

        return result

    except Exception as exc:
        raise RuntimeError(
            f"Barkod eşleme dosyası "
            f"okunamadı: {exc}"
        )


def barcode_number(barcode):
    barcode = clean(barcode)

    if not barcode.startswith(
        BARCODE_PREFIX
    ):
        return None

    suffix = barcode[
        len(BARCODE_PREFIX):
    ]

    if not suffix.isdigit():
        return None

    return int(suffix)


def find_next_barcode_number(
    barcode_map
):
    highest = (
        BARCODE_START - 1
    )

    for barcode in (
        barcode_map.values()
    ):
        number = barcode_number(
            barcode
        )

        if number is not None:
            highest = max(
                highest,
                number
            )

    return highest + 1


def make_barcode(number):
    return (
        BARCODE_PREFIX
        + f"{number:03d}"
    )


def assign_barcodes(products):
    barcode_map = (
        load_barcode_map()
    )

    used_barcodes = set()

    for barcode in (
        barcode_map.values()
    ):
        if barcode in used_barcodes:
            raise RuntimeError(
                "Mükerrer barkod: "
                f"{barcode}"
            )

        used_barcodes.add(
            barcode
        )

    next_number = (
        find_next_barcode_number(
            barcode_map
        )
    )

    new_count = 0

    def sort_key(product):
        product_id = (
            product.get("malzemeId")
            or product.get("id")
        )

        try:
            return (
                0,
                int(product_id)
            )

        except (
            ValueError,
            TypeError
        ):
            return (
                1,
                str(product_id)
            )

    sorted_products = sorted(
        products,
        key=sort_key
    )

    for product in sorted_products:
        product_id = (
            product.get("malzemeId")
            or product.get("id")
        )

        product_id = str(
            product_id
        )

        if (
            product_id
            in barcode_map
        ):
            continue

        while True:
            barcode = make_barcode(
                next_number
            )

            next_number += 1

            if (
                barcode
                not in used_barcodes
            ):
                break

        barcode_map[
            product_id
        ] = barcode

        used_barcodes.add(
            barcode
        )

        new_count += 1

    temp_file = (
        BARCODE_FILE
        + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            barcode_map,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )

    with open(
        temp_file,
        "r",
        encoding="utf-8"
    ) as f:
        json.load(f)

    os.replace(
        temp_file,
        BARCODE_FILE
    )

    print(
        f"Yeni barkod verilen ürün: "
        f"{new_count}"
    )

    print(
        f"Toplam barkod eşlemesi: "
        f"{len(barcode_map)}"
    )

    return barcode_map


# ==================================================
# DETAY CACHE
# ==================================================

def load_detail_cache():
    if not os.path.exists(
        DETAIL_FILE
    ):
        print(
            "Detay cache dosyası "
            "henüz yok."
        )

        return {}

    try:
        with open(
            DETAIL_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):
            raise RuntimeError(
                "Detay cache "
                "formatı geçersiz."
            )

        print(
            f"Cache'de ürün detayı: "
            f"{len(data)}"
        )

        return data

    except Exception as exc:
        raise RuntimeError(
            f"Detay cache okunamadı: "
            f"{exc}"
        )


def save_detail_cache(
    detail_cache
):
    with open(
        DETAIL_TMP_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            detail_cache,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )

    with open(
        DETAIL_TMP_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        json.load(f)

    os.replace(
        DETAIL_TMP_FILE,
        DETAIL_FILE
    )


def fetch_product_detail(
    session,
    cari_id,
    product_id
):
    payload = {
        "cariId": cari_id,
        "cariIdList": "",
        "urunId": int(product_id)
    }

    response = request_with_retry(
        session,
        "POST",
        DETAIL_URL,
        json=payload
    )

    result = response.json()

    if not result.get("success"):
        raise RuntimeError(
            "Detay servisi başarısız: "
            f"{result.get('message')}"
        )

    item = (
        result
        .get("data", {})
        .get("item")
    )

    if not isinstance(
        item,
        dict
    ):
        raise RuntimeError(
            "Detay cevabında "
            "item bulunamadı."
        )

    return {
        "aciklama": clean(
            item.get("aciklama")
        ),
        "teknikOzellikler": clean(
            item.get(
                "teknikOzellikler"
            )
        )
    }


def update_detail_cache(
    session,
    cari_id,
    products
):
    detail_cache = (
        load_detail_cache()
    )

    missing_products = []

    for product in products:
        product_id = (
            product.get("malzemeId")
            or product.get("id")
        )

        product_id = str(
            product_id
        )

        if (
            product_id
            not in detail_cache
        ):
            missing_products.append(
                product
            )

    total_missing = len(
        missing_products
    )

    print(
        f"Detayı eksik ürün: "
        f"{total_missing}"
    )

    if not missing_products:
        print(
            "Tüm ürün detayları "
            "cache'de mevcut."
        )

        return detail_cache

    success_count = 0
    error_count = 0
    processed_count = 0

    total_groups = (
        total_missing
        + DETAIL_GROUP_SIZE
        - 1
    ) // DETAIL_GROUP_SIZE

    for group_start in range(
        0,
        total_missing,
        DETAIL_GROUP_SIZE
    ):
        group = missing_products[
            group_start:
            group_start
            + DETAIL_GROUP_SIZE
        ]

        group_number = (
            group_start
            // DETAIL_GROUP_SIZE
        ) + 1

        print("")
        print(
            "================================"
        )

        print(
            f"DETAY GRUBU "
            f"{group_number}/"
            f"{total_groups}"
        )

        print(
            "================================"
        )

        for index, product in (
            enumerate(
                group,
                start=1
            )
        ):
            product_id = (
                product.get(
                    "malzemeId"
                )
                or product.get("id")
            )

            product_id = str(
                product_id
            )

            try:
                detail = (
                    fetch_product_detail(
                        session,
                        cari_id,
                        product_id
                    )
                )

                detail_cache[
                    product_id
                ] = detail

                success_count += 1

            except Exception as exc:
                error_count += 1

                print(
                    f"DETAY HATA "
                    f"ürün={product_id}: "
                    f"{exc}"
                )

            processed_count += 1

            if index % 25 == 0:
                save_detail_cache(
                    detail_cache
                )

                print(
                    f"Detay ilerleme: "
                    f"{index}/"
                    f"{len(group)}"
                    f" | genel="
                    f"{processed_count}/"
                    f"{total_missing}"
                    f" | başarılı="
                    f"{success_count}"
                    f" | hata="
                    f"{error_count}"
                )

            time.sleep(
                DETAIL_DELAY
            )

        save_detail_cache(
            detail_cache
        )

        remaining = (
            total_missing
            - processed_count
        )

        print(
            f"Detay grubu "
            f"{group_number} "
            f"tamamlandı."
        )

        print(
            f"Kalan detay: "
            f"{max(remaining, 0)}"
        )

        if remaining > 0:
            print(
                f"{GROUP_DELAY} saniye "
                f"bekleniyor..."
            )

            time.sleep(
                GROUP_DELAY
            )

    print(
        f"Detay taraması tamamlandı. "
        f"Başarılı={success_count}, "
        f"Hata={error_count}, "
        f"Cache={len(detail_cache)}"
    )

    return detail_cache


# ==================================================
# GÖRSEL CACHE
# ==================================================

def load_image_cache():
    if not os.path.exists(
        IMAGE_FILE
    ):
        print(
            "Görsel cache dosyası "
            "henüz yok. "
            "İlk görsel taraması yapılacak."
        )

        return {}

    try:
        with open(
            IMAGE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):
            raise RuntimeError(
                "Görsel cache "
                "formatı geçersiz."
            )

        print(
            f"Cache'de görsel bilgisi: "
            f"{len(data)} ürün"
        )

        return data

    except Exception as exc:
        raise RuntimeError(
            f"Görsel cache okunamadı: "
            f"{exc}"
        )


def save_image_cache(
    image_cache
):
    with open(
        IMAGE_TMP_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            image_cache,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )

    # JSON bozuk mu kontrol et
    with open(
        IMAGE_TMP_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        json.load(f)

    os.replace(
        IMAGE_TMP_FILE,
        IMAGE_FILE
    )


def fetch_product_images(
    session,
    product_id
):
    payload = {
        "urunId": int(product_id)
    }

    response = request_with_retry(
        session,
        "POST",
        IMAGE_URL,
        json=payload
    )

    result = response.json()

    if not result.get("success"):
        raise RuntimeError(
            "Görsel servisi başarısız: "
            f"{result.get('message')}"
        )

    data = result.get(
        "data",
        {}
    )

    if not isinstance(
        data,
        dict
    ):
        return []

    image_list = data.get(
        "list",
        []
    )

    if not isinstance(
        image_list,
        list
    ):
        return []

    cleaned_images = []

    for image_item in image_list:
        if not isinstance(
            image_item,
            dict
        ):
            continue

        path = clean(
            image_item.get(
                "resimYolu"
            )
        )

        if not path:
            continue

        cleaned_images.append({
            "resimYolu": path,
            "anaResim": bool(
                image_item.get(
                    "anaResim",
                    False
                )
            ),
            "sira": (
                image_item.get(
                    "sira"
                )
                if image_item.get(
                    "sira"
                ) is not None
                else 999999
            )
        })

    # Önce ana resim.
    # Sonra sıra numarası.
    cleaned_images.sort(
        key=lambda x: (
            0
            if x.get("anaResim")
            else 1,
            x.get("sira", 999999)
        )
    )

    return cleaned_images


def update_image_cache(
    session,
    products
):
    image_cache = (
        load_image_cache()
    )

    missing_products = []

    for product in products:
        product_id = (
            product.get("malzemeId")
            or product.get("id")
        )

        product_id = str(
            product_id
        )

        # Ürün daha önce tarandıysa
        # tekrar API çağrısı yapma.
        # [] olması ürünün görseli
        # olmadığı anlamına gelir.
        if (
            product_id
            not in image_cache
        ):
            missing_products.append(
                product
            )

    total_missing = len(
        missing_products
    )

    print(
        f"Görseli henüz taranmamış ürün: "
        f"{total_missing}"
    )

    if not missing_products:
        print(
            "Tüm ürünlerin görsel bilgisi "
            "cache'de mevcut."
        )

        return image_cache

    success_count = 0
    error_count = 0
    products_with_images = 0
    products_without_images = 0
    processed_count = 0

    total_groups = (
        total_missing
        + IMAGE_GROUP_SIZE
        - 1
    ) // IMAGE_GROUP_SIZE

    for group_start in range(
        0,
        total_missing,
        IMAGE_GROUP_SIZE
    ):
        group = missing_products[
            group_start:
            group_start
            + IMAGE_GROUP_SIZE
        ]

        group_number = (
            group_start
            // IMAGE_GROUP_SIZE
        ) + 1

        print("")
        print(
            "================================"
        )

        print(
            f"GÖRSEL GRUBU "
            f"{group_number}/"
            f"{total_groups}"
        )

        print(
            f"Bu gruptaki ürün: "
            f"{len(group)}"
        )

        print(
            "================================"
        )

        for index, product in (
            enumerate(
                group,
                start=1
            )
        ):
            product_id = (
                product.get(
                    "malzemeId"
                )
                or product.get("id")
            )

            product_id = str(
                product_id
            )

            try:
                images = (
                    fetch_product_images(
                        session,
                        product_id
                    )
                )

                image_cache[
                    product_id
                ] = images

                success_count += 1

                if images:
                    products_with_images += 1

                else:
                    products_without_images += 1

            except Exception as exc:
                # Hata alan ürünü cache'e
                # yazmıyoruz.
                # Böylece sonraki çalışmada
                # tekrar denenir.
                error_count += 1

                print(
                    f"GÖRSEL HATA "
                    f"ürün={product_id}: "
                    f"{exc}"
                )

            processed_count += 1

            # Her 25 üründe bir
            # cache'i kaydet.
            if index % 25 == 0:
                save_image_cache(
                    image_cache
                )

                print(
                    f"Görsel ilerleme: "
                    f"{index}/"
                    f"{len(group)}"
                    f" | genel="
                    f"{processed_count}/"
                    f"{total_missing}"
                    f" | başarılı="
                    f"{success_count}"
                    f" | hata="
                    f"{error_count}"
                    f" | görselli="
                    f"{products_with_images}"
                    f" | görselsiz="
                    f"{products_without_images}"
                )

            time.sleep(
                IMAGE_DELAY
            )

        save_image_cache(
            image_cache
        )

        remaining = (
            total_missing
            - processed_count
        )

        print(
            f"Görsel grubu "
            f"{group_number} "
            f"tamamlandı."
        )

        print(
            f"Cache toplam ürün: "
            f"{len(image_cache)}"
        )

        print(
            f"Kalan görsel taraması: "
            f"{max(remaining, 0)}"
        )

        if remaining > 0:
            print(
                f"{GROUP_DELAY} saniye "
                f"bekleniyor..."
            )

            print(
                "Sonraki görsel grubuna "
                "otomatik geçilecek."
            )

            time.sleep(
                GROUP_DELAY
            )

    print("")
    print(
        "================================"
    )

    print(
        "GÖRSEL TARAMASI TAMAMLANDI"
    )

    print(
        "================================"
    )

    print(
        f"Başarılı sorgu: "
        f"{success_count}"
    )

    print(
        f"Hatalı sorgu: "
        f"{error_count}"
    )

    print(
        f"Görseli bulunan ürün: "
        f"{products_with_images}"
    )

    print(
        f"Görseli olmayan ürün: "
        f"{products_without_images}"
    )

    print(
        f"Görsel cache toplam: "
        f"{len(image_cache)}"
    )

    return image_cache


# ==================================================
# GÖRSEL URL OLUŞTURMA
# ==================================================

def make_image_url(path):
    path = clean(path)

    if not path:
        return ""

    if (
        path.startswith("http://")
        or path.startswith("https://")
    ):
        return path

    path = path.lstrip("/")

    return (
        IMAGE_FILE_BASE_URL
        + "/"
        + path
    )


def get_product_picture_urls(
    image_cache,
    product_id
):
    images = image_cache.get(
        str(product_id),
        []
    )

    if not isinstance(
        images,
        list
    ):
        return []

    # Tekrar garanti sıralaması
    images = sorted(
        images,
        key=lambda x: (
            0
            if x.get("anaResim")
            else 1,
            x.get("sira", 999999)
        )
    )

    urls = []

    for image_item in images:
        path = clean(
            image_item.get(
                "resimYolu"
            )
        )

        url = make_image_url(
            path
        )

        if (
            url
            and url not in urls
        ):
            urls.append(url)

        if len(urls) >= 4:
            break

    return urls


# ==================================================
# AÇIKLAMA
# ==================================================

def fallback_description(
    name,
    brand,
    category,
    subcategory,
    unit
):
    parts = []

    if name:
        parts.append(
            "<p><strong>"
            + html_clean(name)
            + "</strong></p>"
        )

    features = []

    if brand:
        features.append(
            "<li><strong>Marka:</strong> "
            + html_clean(brand)
            + "</li>"
        )

    if category:
        features.append(
            "<li><strong>Kategori:</strong> "
            + html_clean(category)
            + "</li>"
        )

    if subcategory:
        features.append(
            "<li><strong>Alt Kategori:</strong> "
            + html_clean(subcategory)
            + "</li>"
        )

    if unit:
        features.append(
            "<li><strong>Birim:</strong> "
            + html_clean(unit)
            + "</li>"
        )

    if features:
        parts.append(
            "<ul>"
            + "".join(features)
            + "</ul>"
        )

    return "".join(parts)


def create_real_description(
    detail,
    name,
    brand,
    category,
    subcategory,
    unit
):
    if not isinstance(
        detail,
        dict
    ):
        return fallback_description(
            name,
            brand,
            category,
            subcategory,
            unit
        )

    aciklama = clean(
        detail.get(
            "aciklama"
        )
    )

    teknik = clean(
        detail.get(
            "teknikOzellikler"
        )
    )

    if (
        not aciklama
        and not teknik
    ):
        return fallback_description(
            name,
            brand,
            category,
            subcategory,
            unit
        )

    parts = []

    if name:
        parts.append(
            "<h3>"
            + html_clean(name)
            + "</h3>"
        )

    if aciklama:
        parts.append(
            "<h4>Ürün Açıklaması</h4>"
        )

        parts.append(
            aciklama
        )

    if teknik:
        parts.append(
            "<h4>Teknik Özellikler</h4>"
        )

        parts.append(
            teknik
        )

    return "".join(parts)


# ==================================================
# XML
# ==================================================

def add_text(
    parent,
    tag,
    value
):
    node = ET.SubElement(
        parent,
        tag
    )

    node.text = clean(
        value
    )

    return node


def build_xml(
    products,
    barcode_map,
    detail_cache,
    image_cache
):
    root = ET.Element(
        "root"
    )

    barcode_count = 0

    real_detail_count = 0
    fallback_count = 0

    image_product_count = 0
    total_image_count = 0

    for p in products:
        item = ET.SubElement(
            root,
            "item"
        )

        product_id = (
            p.get("malzemeId")
            or p.get("id")
        )

        product_id = str(
            product_id
        )

        code = p.get(
            "malzemeKodu"
        )

        name = p.get(
            "malzemeAciklama"
        )

        brand = p.get(
            "marka"
        )

        category = p.get(
            "kategori"
        )

        subcategory = p.get(
            "altKategori"
        )

        vat = p.get(
            "kdvOran"
        )

        unit = p.get(
            "birim"
        )

        stock = int_stock(
            p.get("stok")
        )

        net_price = decimal_text(
            p.get("bayiFiyati"),
            6
        )

        barcode = barcode_map.get(
            product_id,
            ""
        )

        if not barcode:
            raise RuntimeError(
                f"Ürünün barkodu "
                f"bulunamadı: "
                f"{product_id}"
            )

        barcode_count += 1

        detail = detail_cache.get(
            product_id
        )

        if (
            isinstance(
                detail,
                dict
            )
            and (
                clean(
                    detail.get(
                        "aciklama"
                    )
                )
                or clean(
                    detail.get(
                        "teknikOzellikler"
                    )
                )
            )
        ):
            real_detail_count += 1

        else:
            fallback_count += 1

        description = (
            create_real_description(
                detail,
                name,
                brand,
                category,
                subcategory,
                unit
            )
        )

        picture_urls = (
            get_product_picture_urls(
                image_cache,
                product_id
            )
        )

        if picture_urls:
            image_product_count += 1
            total_image_count += len(
                picture_urls
            )

        add_text(
            item,
            "id",
            product_id
        )

        add_text(
            item,
            "code",
            code
        )

        add_text(
            item,
            "label",
            name
        )

        add_text(
            item,
            "stock",
            stock
        )

        add_text(
            item,
            "details",
            description
        )

        add_text(
            item,
            "currency",
            "TRL"
        )

        add_text(
            item,
            "price1",
            net_price
        )

        add_text(
            item,
            "tax",
            decimal_text(
                vat,
                2
            )
        )

        add_text(
            item,
            "barcode",
            barcode
        )

        add_text(
            item,
            "brand",
            brand
        )

        add_text(
            item,
            "mainCategory",
            category
        )

        add_text(
            item,
            "category",
            subcategory
        )

        add_text(
            item,
            "unit",
            unit
        )

        add_text(
            item,
            "coefficient",
            decimal_text(
                p.get("katsayi"),
                6
            )
        )

        add_text(
            item,
            "listPrice",
            decimal_text(
                p.get("fiyat"),
                6
            )
        )

        add_text(
            item,
            "discount",
            decimal_text(
                p.get("iskonto"),
                2
            )
        )

        add_text(
            item,
            "cashDiscount",
            decimal_text(
                p.get(
                    "nakitIskonto"
                ),
                2
            )
        )

        add_text(
            item,
            "creditCardDiscount",
            decimal_text(
                p.get(
                    "krediKartiIskonto"
                ),
                2
            )
        )

        add_text(
            item,
            "vatIncluded",
            decimal_text(
                p.get("kdvDahil"),
                6
            )
        )

        # ==================================================
        # GÖRSELLER
        # ==================================================

        for picture_number in range(
            1,
            5
        ):
            index = (
                picture_number - 1
            )

            if index < len(
                picture_urls
            ):
                picture_url = (
                    picture_urls[
                        index
                    ]
                )

            else:
                picture_url = ""

            add_text(
                item,
                f"picture{picture_number}",
                picture_url
            )

    # ==================================================
    # XML GÜVENLİK KONTROLÜ
    # ==================================================

    if (
        barcode_count
        != len(products)
    ):
        raise RuntimeError(
            f"Barkod sayısı hatalı. "
            f"Ürün={len(products)}, "
            f"Barkod={barcode_count}"
        )

    tree = ET.ElementTree(
        root
    )

    try:
        ET.indent(
            tree,
            space="  "
        )

    except AttributeError:
        pass

    tree.write(
        TMP_FILE,
        encoding="utf-8",
        xml_declaration=True
    )

    # XML parse kontrolü
    check_root = ET.parse(
        TMP_FILE
    ).getroot()

    xml_count = len(
        check_root.findall(
            "item"
        )
    )

    if (
        xml_count
        != len(products)
    ):
        raise RuntimeError(
            f"XML ürün sayısı hatalı. "
            f"Beklenen="
            f"{len(products)}, "
            f"XML={xml_count}"
        )

    xml_barcodes = []

    for item in (
        check_root.findall(
            "item"
        )
    ):
        barcode_node = (
            item.find(
                "barcode"
            )
        )

        barcode = (
            clean(
                barcode_node.text
            )
            if barcode_node
            is not None
            else ""
        )

        if not barcode:
            raise RuntimeError(
                "XML içinde "
                "barkodsuz ürün var."
            )

        xml_barcodes.append(
            barcode
        )

    if (
        len(xml_barcodes)
        != len(
            set(xml_barcodes)
        )
    ):
        raise RuntimeError(
            "XML içinde mükerrer "
            "barkod var."
        )

    # Her şey sağlamsa
    # eski XML'in üzerine geç.
    os.replace(
        TMP_FILE,
        OUTPUT_FILE
    )

    print("")
    print(
        "================================"
    )

    print(
        "XML OLUŞTURMA TAMAMLANDI"
    )

    print(
        "================================"
    )

    print(
        f"XML ürün sayısı: "
        f"{len(products)}"
    )

    print(
        f"Barkodlu ürün: "
        f"{barcode_count}/"
        f"{len(products)}"
    )

    print(
        "Gerçek açıklama/teknik "
        f"özellik kullanılan: "
        f"{real_detail_count}"
    )

    print(
        f"Geçici açıklama kullanılan: "
        f"{fallback_count}"
    )

    print(
        f"Görseli XML'e eklenen ürün: "
        f"{image_product_count}"
    )

    print(
        f"XML'e eklenen toplam görsel: "
        f"{total_image_count}"
    )


# ==================================================
# ANA PROGRAM
# ==================================================

def main():
    session = requests.Session()

    # 1. Giriş
    cari_id = login(
        session
    )

    # 2. Güncel ürün listesini çek
    products = fetch_all_products(
        session,
        cari_id
    )

    print(
        f"Tüm ürünler başarıyla "
        f"çekildi: "
        f"{len(products)}"
    )

    # 3. Barkodlar
    barcode_map = (
        assign_barcodes(
            products
        )
    )

    # 4. Açıklama / teknik özellik
    detail_cache = (
        update_detail_cache(
            session,
            cari_id,
            products
        )
    )

    # 5. Görseller
    image_cache = (
        update_image_cache(
            session,
            products
        )
    )

    # 6. XML
    build_xml(
        products,
        barcode_map,
        detail_cache,
        image_cache
    )

    print(
        f"XML başarıyla oluşturuldu: "
        f"{OUTPUT_FILE}"
    )


# ==================================================
# ÇALIŞTIR
# ==================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            f"HATA: {exc}",
            file=sys.stderr
        )

        # Geçici XML
        if os.path.exists(
            TMP_FILE
        ):
            os.remove(
                TMP_FILE
            )

        # Geçici barkod
        temp_barcode_file = (
            BARCODE_FILE
            + ".tmp"
        )

        if os.path.exists(
            temp_barcode_file
        ):
            os.remove(
                temp_barcode_file
            )

        # Geçici detay
        if os.path.exists(
            DETAIL_TMP_FILE
        ):
            os.remove(
                DETAIL_TMP_FILE
            )

        # Geçici görsel
        if os.path.exists(
            IMAGE_TMP_FILE
        ):
            os.remove(
                IMAGE_TMP_FILE
            )

        sys.exit(1)
