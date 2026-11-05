import requests


class Nmt:
    def __init__(self):
        pass

    def infer(self, text: str, source_lang: str, target_lang: str):
        payload = {
            "text": text,
            "src_lang": source_lang,
            "tgt_lang": target_lang
        }

        response = requests.post("http://localhost:11400/nmt", json=payload)

        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"NMT inference failed: {response.text}")
