import os

import requests


def get_gcals():
    if url := os.getenv("GCAL_URL"):
        response = requests.get(url)
        if response.ok:
            return response.json()
    return {}
