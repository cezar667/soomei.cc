# scripts/write_tags.py — gravação em lote de slugs em NTAG213/215
# pip install nfcpy
import nfc, ndef, time, csv, sys
def on_connect(tag, slug):
    uri = f"https://smei.cc/{slug}"
    record = ndef.UriRecord(uri)
    message = ndef.Message(record)
    try:
        tag.ndef.message = message
        print(f"[OK] Gravado: {uri}")
        return True
    except Exception as e:
        print("[ERRO]", e)
        return False
def main(csv_path="slugs.csv"):
    with open(csv_path, newline='', encoding='utf-8') as f:
        rows = [r["slug"] for r in csv.DictReader(f)]
    for slug in rows:
        print(f"== Aproxime o cartão para gravar: {slug}")
        with nfc.ContactlessFrontend('usb') as clf:
            clf.connect(rdwr={'on-connect': lambda tag: on_connect(tag, slug)})
            time.sleep(0.5)
if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "slugs.csv"
    main(path)
