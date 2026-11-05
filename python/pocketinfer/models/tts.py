import requests


class Tts:
    def __init__(self):
        pass

    def infer(self, text: str, language: str):
        payload = {
            "text": text,
            "language": language
        }

        response = requests.post("http://localhost:11400/tts", json=payload)

        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"TTS inference failed: {response.text}")
