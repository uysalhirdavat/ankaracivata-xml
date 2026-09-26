import os
import sys
import time
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import requests


BASE_URL = "https://b2b.ankaracivata.com.tr"
LOGIN_URL = f"{BASE_URL}/api/Kullanici/login"
PRODUCTS_URL = f"{BASE_URL}/api/Malzeme/getAll"

OUTPUT_FILE = "ankaracivata.xml"
TMP_FILE = "ankaracivata.xml.tmp"

PAGE_SIZE = 1000
TIMEOUT = 60
MAX_RETRIES = 4


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def decimal_text(value, places=6):
    try:
        d = Decimal(str(value))
        return f"{d:.{places}f}".rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError, TypeError):
        return "0"


def int_stock(value):
    try:
        d = Decimal(str(value))
        if d < 0:
            return 0
        return int(d)
    except (InvalidOperation, ValueError, TypeError):
        return 0


def request_with_retry(session, method, url, **kwargs):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
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
                f"HTTP {response.status_code}: {response.text[:300]}"
            )

        except requests.RequestException as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            wait = attempt * 3
            print(f"İstek başarısız. {wait} sn sonra tekrar deneniyor...")
            time.sleep(wait)

    raise RuntimeError(f"İstek başarısız: {last_error}")


def login(session):
    username = os.environ.get("ANKARA_KULLANICI_ADI")
    password = os.environ.get("ANKARA_SIFRE")

    if not username or not password:
        raise RuntimeError(
            "ANKARA_KULLANICI_ADI veya ANKARA_SIFRE GitHub Secret bulunamadı."
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
            f"Giriş başarısız: {data.get('message')}"
        )

    token_data = data.get("data", {}).get("token", {})
    token = token_data.get("token")
    cari_id = token_data.get("cariId")

    if not token or cari_id is None:
        raise RuntimeError("Login cevabından token/cariId alınamadı.")

    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json"
    })

    print("Ankara Civata girişi başarılı.")
    print("Cari ID alındı. Token loglanmayacak.")

    return cari_id


def fetch_page(session, cari_id, skip):
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
            f"Ürün servisi başarısız: {result.get('message')}"
        )

    load_result = result.get("data", {}).get("loadResult", {})

    products = load_result.get("data", [])
    total_count = load_result.get("totalCount")

    if total_count is None:
        raise RuntimeError("API totalCount döndürmedi.")

    return products, int(total_count)


def fetch_all_products(session, cari_id):
    all_products = []
    seen_ids = set()
    skip = 0
    expected_total = None

    while True:
        products, total_count = fetch_page(
            session,
            cari_id,
            skip
        )

        if expected_total is None:
            expected_total = total_count
            print(f"API toplam ürün: {expected_total}")

        elif total_count != expected_total:
            raise RuntimeError(
                f"Toplam ürün sayısı işlem sırasında değişti: "
                f"{expected_total} -> {total_count}"
            )

        if not products:
            break

        for product in products:
            product_id = product.get("malzemeId") or product.get("id")

            if product_id is None:
                raise RuntimeError(
                    f"Ürün ID bulunamadı: {product}"
                )

            product_id = str(product_id)

            if product_id in seen_ids:
                continue

            seen_ids.add(product_id)
            all_products.append(product)

        print(
            f"Çekilen: {len(all_products)} / {expected_total} "
            f"(skip={skip}, gelen={len(products)})"
        )

        skip += len(products)

        if len(all_products) >= expected_total:
            break

        if len(products) < PAGE_SIZE:
            break

        time.sleep(0.4)

    if expected_total is None:
        raise RuntimeError("Toplam ürün sayısı alınamadı.")

    if len(all_products) != expected_total:
        raise RuntimeError(
            f"EKSİK VERİ! API toplam={expected_total}, "
            f"benzersiz çekilen={len(all_products)}. "
            f"XML güncellenmeyecek."
        )

    return all_products


def show_product_fields(products):
    print("")
    print("========================================")
    print("ANKARA CIVATA API URUN ALANLARI")
    print("========================================")

    if not products:
        print("Ürün bulunamadı.")
        return

    print("API'den gelen alan adları:")

    for key in sorted(products[0].keys()):
        print(f" - {key}")

    print("")
    print("========================================")
    print("78477 URUNUNUN TUM ALANLARI")
    print("========================================")

    target = next(
        (
            product
            for product in products
            if str(
                product.get("malzemeId")
                or product.get("id")
            ) == "78477"
        ),
        None
    )

    if target is None:
        print("78477 ID'li ürün bulunamadı.")
        return

    for key in sorted(target.keys()):
        value = target.get(key)

        # Hassas olabilecek alanları loglama.
        key_lower = str(key).lower()

        if any(
            word in key_lower
            for word in (
                "token",
                "sifre",
                "password",
                "authorization",
                "cookie",
                "session"
            )
        ):
            print(f"{key}: [GİZLENDİ]")
        else:
            print(f"{key}: {value}")

    print("========================================")
    print("78477 ALAN TARAMASI")
    print("========================================")

    possible_barcode_fields = []

    for key, value in target.items():
        key_lower = str(key).lower()

        if any(
            word in key_lower
            for word in (
                "barkod",
                "barcode",
                "ean",
                "gtin",
                "upc"
            )
        ):
            possible_barcode_fields.append(
                (key, value)
            )

    if possible_barcode_fields:
        print("Olası barkod alanları bulundu:")

        for key, value in possible_barcode_fields:
            print(f"{key}: {value}")
    else:
        print(
            "Bu ürün kaydında barkod/barcode/ean/"
            "gtin/upc isimli alan bulunamadı."
        )

    print("========================================")
    print("")


def add_text(parent, tag, value):
    node = ET.SubElement(parent, tag)
    node.text = clean(value)
    return node


def get_barcode(product):
    possible_keys = (
        "barkod",
        "barcode",
        "barkodNo",
        "barkodKodu",
        "ean",
        "ean13",
        "gtin",
        "upc"
    )

    for key in possible_keys:
        value = clean(product.get(key))

        if value:
            return value

    return ""


def build_xml(products):
    root = ET.Element("root")

    barcode_count = 0

    for p in products:
        item = ET.SubElement(root, "item")

        product_id = p.get("malzemeId") or p.get("id")
        code = p.get("malzemeKodu")
        name = p.get("malzemeAciklama")
        brand = p.get("marka")
        category = p.get("kategori")
        subcategory = p.get("altKategori")
        vat = p.get("kdvOran")
        unit = p.get("birim")
        stock = int_stock(p.get("stok"))

        # Ankara Civata API'deki bayiFiyati
        # KDV hariç net bayi fiyatıdır.
        net_price = decimal_text(
            p.get("bayiFiyati"),
            6
        )

        barcode = get_barcode(p)

        if barcode:
            barcode_count += 1

        add_text(item, "id", product_id)
        add_text(item, "code", code)
        add_text(item, "label", name)
        add_text(item, "stock", stock)

        description_parts = []

        if brand:
            description_parts.append(
                f"Marka: {clean(brand)}"
            )

        if category:
            description_parts.append(
                f"Kategori: {clean(category)}"
            )

        if subcategory:
            description_parts.append(
                f"Alt Kategori: {clean(subcategory)}"
            )

        if unit:
            description_parts.append(
                f"Birim: {clean(unit)}"
            )

        add_text(
            item,
            "details",
            " | ".join(description_parts)
        )

        add_text(item, "currency", "TRL")
        add_text(item, "price1", net_price)
        add_text(
            item,
            "tax",
            decimal_text(vat, 2)
        )
        add_text(item, "barcode", barcode)
        add_text(item, "brand", brand)
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

        # Kontrol / referans alanları
        add_text(item, "unit", unit)

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
                p.get("nakitIskonto"),
                2
            )
        )

        add_text(
            item,
            "creditCardDiscount",
            decimal_text(
                p.get("krediKartiIskonto"),
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

        image_path = clean(
            p.get("resimYolu")
        )

        if image_path:
            if (
                image_path.startswith("http://")
                or image_path.startswith("https://")
            ):
                image_url = image_path

            elif image_path.startswith("/"):
                image_url = (
                    BASE_URL + image_path
                )

            else:
                image_url = (
                    BASE_URL
                    + "/"
                    + image_path
                )

            add_text(
                item,
                "picture1",
                image_url
            )

        else:
            add_text(
                item,
                "picture1",
                ""
            )

        add_text(item, "picture2", "")
        add_text(item, "picture3", "")
        add_text(item, "picture4", "")

    tree = ET.ElementTree(root)

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

    # XML'in gerçekten geçerli olduğunu kontrol et.
    ET.parse(TMP_FILE)

    # Tam XML başarıyla oluşmadan mevcut XML'e dokunma.
    os.replace(
        TMP_FILE,
        OUTPUT_FILE
    )

    print(
        f"Barkod bulunan ürün sayısı: "
        f"{barcode_count} / {len(products)}"
    )


def main():
    session = requests.Session()

    cari_id = login(session)

    products = fetch_all_products(
        session,
        cari_id
    )

    print(
        f"Tüm ürünler başarıyla çekildi: "
        f"{len(products)}"
    )

    # API'nin ürün kaydındaki tüm alanları kontrol et.
    show_product_fields(products)

    build_xml(products)

    print(
        f"XML başarıyla oluşturuldu: "
        f"{OUTPUT_FILE}"
    )

    print(
        f"XML ürün sayısı: "
        f"{len(products)}"
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            f"HATA: {exc}",
            file=sys.stderr
        )

        if os.path.exists(TMP_FILE):
            os.remove(TMP_FILE)

        sys.exit(1)
